from pathlib import Path

path = Path("pipeline.py")
text = path.read_text(encoding="utf-8")

helper = '''\n\ndef read_http_error_body(exc: urllib.error.HTTPError, limit: int = 2000) -> str:\n    try:\n        body = exc.read()\n    except Exception:\n        return ""\n    if isinstance(body, bytes):\n        text = body.decode("utf-8", errors="replace")\n    else:\n        text = str(body)\n    return shorten_text(text, limit=limit)\n\n\ndef format_http_error(exc: urllib.error.HTTPError, body: str) -> str:\n    detail = f"HTTP Error {exc.code}: {exc.reason}"\n    if body:\n        return f"{detail} | response body: {body}"\n    return detail\n'''

anchor = '''def build_thinking_config(section: Dict) -> Dict:\n'''
if "def read_http_error_body(" not in text:
    if anchor not in text:
        raise SystemExit("Could not find build_thinking_config anchor")
    text = text.replace(anchor, helper + "\n" + anchor, 1)

replacements = [
    (
        '''        except urllib.error.HTTPError as exc:\n            last_error = exc\n            if exc.code == 429:\n''',
        '''        except urllib.error.HTTPError as exc:\n            error_body = read_http_error_body(exc)\n            last_error = format_http_error(exc, error_body)\n            if exc.code == 429:\n''',
    ),
    (
        '''        except urllib.error.HTTPError as exc:\n            fallback_error = exc\n            if exc.code == 429:\n''',
        '''        except urllib.error.HTTPError as exc:\n            error_body = read_http_error_body(exc)\n            fallback_error = format_http_error(exc, error_body)\n            if exc.code == 429:\n''',
    ),
]

for old, new in replacements:
    text = text.replace(old, new)

# Add body details to every HTTPError retry log. The replacements are scoped by nearby HTTP status fields,
# so normal non-HTTP exception logs are left untouched.
text = text.replace(
    '''                    "wait_seconds": wait,\n                    "error": str(exc),\n                })\n''',
    '''                    "wait_seconds": wait,\n                    "error": last_error,\n                    "http_error_body": error_body,\n                })\n''',
)
text = text.replace(
    '''                "wait_seconds": min(2.0 ** attempt, 60) + random.uniform(0, 1),\n                "error": str(exc),\n            })\n''',
    '''                "wait_seconds": min(2.0 ** attempt, 60) + random.uniform(0, 1),\n                "error": last_error,\n                "http_error_body": error_body,\n            })\n''',
)
text = text.replace(
    '''                "wait_seconds": wait,\n                "error": str(exc),\n            })\n''',
    '''                "wait_seconds": wait,\n                "error": last_error,\n                "http_error_body": error_body,\n            })\n''',
)

# The classification fallback block uses fallback_error, not last_error.
text = text.replace(
    '''                    "step": "classification_fallback_retry",\n                    "attempt": attempt,\n                    "http_status": exc.code,\n                    "retry_after": retry_after,\n                    "wait_seconds": wait,\n                    "error": last_error,\n                    "http_error_body": error_body,\n                })\n''',
    '''                    "step": "classification_fallback_retry",\n                    "attempt": attempt,\n                    "http_status": exc.code,\n                    "retry_after": retry_after,\n                    "wait_seconds": wait,\n                    "error": fallback_error,\n                    "http_error_body": error_body,\n                })\n''',
)
text = text.replace(
    '''                "step": "classification_fallback_retry",\n                "attempt": attempt,\n                "http_status": exc.code,\n                "retry_after": None,\n                "wait_seconds": wait,\n                "error": last_error,\n                "http_error_body": error_body,\n            })\n''',
    '''                "step": "classification_fallback_retry",\n                "attempt": attempt,\n                "http_status": exc.code,\n                "retry_after": None,\n                "wait_seconds": wait,\n                "error": fallback_error,\n                "http_error_body": error_body,\n            })\n''',
)

if "read_http_error_body" not in text or "http_error_body" not in text:
    raise SystemExit("Patch did not apply expected markers")

path.write_text(text, encoding="utf-8")
print("Applied HTTP error body logging to pipeline.py")
