"""Pinned experimental transport patch; not installed in the execution backend."""
import hashlib
import inspect


def install():
    import httpx._transports.jsfetch as transport

    expected = '8710118e879ea03aad9782f7585426b7331f3933cbef465809416c4f4cfe9e48'
    if hashlib.sha256(inspect.getsource(transport).encode()).hexdigest() != expected:
        raise RuntimeError('Unqualified HTTPX transport source')
    original = inspect.getsource(transport._do_fetch)
    marker = '"signal": abort_controller_js.signal,'
    if original.count(marker) != 1:
        raise RuntimeError('Unqualified HTTPX fetch function')
    # Keep the actual upstream request construction. Let HTTPX own redirects,
    # history, method transitions and credential stripping rather than JS fetch.
    patched = original.replace(marker, marker + '\n        "redirect": "manual",')
    namespace = dict(vars(transport))
    exec(compile(patched, '<pinned-httpx-redirect-patch>', 'exec'), namespace)
    transport._do_fetch = namespace['_do_fetch']
    return {'original_transport_sha256': expected,
            'patched_function_sha256': hashlib.sha256(patched.encode()).hexdigest()}
