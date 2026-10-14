"""Read-only, paged access to archived SMS. No ingestion or mutation endpoints."""
import re
import unicodedata
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter()

# Only copy a single unambiguous numeric code associated with an OTP label.
_LABEL = r'(?:验证码|校验码|动态码|动态密码|短信密码|一次性密码|verification\s+code|security\s+code|one[- ]time\s+(?:code|password)|\botp\b|\bcode\b)'
_AFTER = re.compile(_LABEL + r'[^0-9\r\n。.!！?？]{0,24}?(?<![A-Za-z0-9])([0-9]{4,8})(?![A-Za-z0-9])', re.I)
_BEFORE = re.compile(r'(?<![A-Za-z0-9])([0-9]{4,8})(?![A-Za-z0-9])(?:\s|[，,:：;；()（）-]|是|为|您|的|本次|登录|登陆|验证|is|your){0,20}' + _LABEL, re.I)

def verification_code(body):
    text = unicodedata.normalize('NFKC', body or '')
    codes = set(_AFTER.findall(text)) or set(_BEFORE.findall(text))
    return next(iter(codes)) if len(codes) == 1 else ''


@router.get('/api/sms')
def sms_list(offset: int = Query(0, ge=0, le=1000000), limit: int = Query(40, ge=1, le=100), q: str = Query('', max_length=100)):
    from .main import postgres_connection
    connection = None
    try:
        connection = postgres_connection('nasbridge-sms-read')
        connection.set_session(readonly=True)
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '5s'")
            term = q.strip()
            pattern = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            where = " WHERE sender ILIKE %s OR body ILIKE %s" if term else ''
            args = (pattern, pattern) if term else ()
            cursor.execute('SELECT device_id, message_id, sender, LEFT(body, 16000), sent_at, captured_at, source, received_at, length(body) > 16000 FROM message_redirect.sms_messages' + where + ' ORDER BY sent_at DESC NULLS LAST, received_at DESC NULLS LAST, device_id, message_id LIMIT %s OFFSET %s', args + (limit + 1, offset))
            rows = cursor.fetchall()
        items=[]
        for row in rows[:limit]:
            items.append(dict(zip(('device_id','message_id','sender','body','sent_at','captured_at','source','received_at','truncated'), [str(v) if i in (0,1) else v.isoformat() if i in (4,5,7) and v is not None else v for i,v in enumerate(row)])))
        for item in items:
            item['verification_code'] = verification_code(item['body'])
        return JSONResponse({'items':items,'offset':offset,'limit':limit,'has_more':len(rows)>limit}, headers={'Cache-Control':'no-store'})
    except Exception:
        raise HTTPException(503, '短信读取失败，请检查数据库连接与短信表权限') from None
    finally:
        if connection is not None: connection.close()
