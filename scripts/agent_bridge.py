"""stdio MCP bridge for clients without custom HTTP Bearer headers.

Configure SMARTPLATE_MCP_URL and SMARTPLATE_AGENT_TOKEN as connector secrets.
The bridge never receives a recovery code or a Swiggy token. No automatic retries.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the connector token to a redirected host.


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    url, token = os.environ.get('SMARTPLATE_MCP_URL', ''), os.environ.get('SMARTPLATE_AGENT_TOKEN', '')
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost','127.0.0.1'))) or parsed.username or parsed.password or parsed.query or parsed.fragment or not token:
        print('Configure an HTTPS MCP URL (or localhost) and an agent token in connector secrets.', file=sys.stderr)
        return 2
    opener, version = urllib.request.build_opener(NoRedirect()), '2025-11-25'
    while True:
        line = sys.stdin.buffer.readline(256 * 1024 + 1)
        if not line:
            break
        if len(line) > 256 * 1024:
            print('MCP input exceeds 256 KB.',file=sys.stderr)
            return 2
        identifier, message = None, None
        try:
            message = json.loads(line)
            identifier = message.get('id')
            req = urllib.request.Request(url,data=json.dumps(message,allow_nan=False).encode('utf-8'),
                headers={'Authorization':'Bearer '+token,'Content-Type':'application/json',
                         'Accept':'application/json, text/event-stream','MCP-Protocol-Version':version})
            with opener.open(req, timeout=90) as response:
                if response.status == 202:
                    continue
                data=response.read(2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError('MCP response exceeds 2 MB')
                result=json.loads(data)
            if message.get('method')=='initialize' and result.get('result',{}).get('protocolVersion'):
                version=result['result']['protocolVersion']
        except Exception as exc:
            if isinstance(message,dict) and 'id' not in message:
                continue
            # No URLs, Authorization headers, response bodies or secrets in logs.
            result={'jsonrpc':'2.0','id':identifier,'error':{'code':-32000,
                'message':'SmartPlate connection failed ('+type(exc).__name__+'). Check the endpoint, connection expiry and permissions. Do not retry a purchase automatically.'}}
        sys.stdout.write(json.dumps(result,ensure_ascii=False)+'\n')
        sys.stdout.flush()
    return 0


if __name__=='__main__':
    sys.exit(main())
