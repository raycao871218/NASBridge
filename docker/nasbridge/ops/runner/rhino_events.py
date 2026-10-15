"""Small structured progress events; never include subprocess logs or credentials."""
import json

PREFIX = 'NASBRIDGE_PROGRESS '


def event(stage, state='running', message='', **fields):
    print(PREFIX + json.dumps(dict(stage=stage, state=state, message=message, **fields), ensure_ascii=False), flush=True)
