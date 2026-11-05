"""Private-CA control for a disposable production container running as root.

Mount core/test_python_trust_store.py at /trust_test.py. This check mutates
only the disposable container's CA store and opens only a loopback TLS server.
"""
import datetime
import hashlib
import os
from pathlib import Path
import socket
import ssl
import subprocess
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def client_context(**kwargs):
    context = ssl.create_default_context(**kwargs)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


print("PRODUCTION", ssl.OPENSSL_VERSION, flush=True)
subprocess.run(["python", "-B", "/trust_test.py", "-v"], check=True)
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Private CA Compatibility Fixture")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (
    x509.CertificateBuilder()
    .subject_name(name).issuer_name(name).public_key(key.public_key())
    .serial_number(x509.random_serial_number())
    .not_valid_before(now - datetime.timedelta(minutes=1))
    .not_valid_after(now + datetime.timedelta(days=1))
    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
    .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
    .sign(key, hashes.SHA256())
)
pem = cert.public_bytes(serialization.Encoding.PEM)
Path("/usr/local/share/ca-certificates/pr1235-private.crt").write_bytes(pem)
subprocess.run(["update-ca-certificates"], check=True, stdout=subprocess.DEVNULL)
fingerprint = cert.fingerprint(hashes.SHA256()).hex()


def contains(context):
    return fingerprint in {
        hashlib.sha256(c).hexdigest() for c in context.get_ca_certs(binary_form=True)
    }


assert contains(client_context(cafile="/etc/ssl/certs/ca-certificates.crt"))
assert contains(client_context())
Path("/tmp/pr1235-cert.pem").write_bytes(pem)
Path("/tmp/pr1235-key.pem").write_bytes(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
))


def handshake(client_context):
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_2
    server_context.load_cert_chain("/tmp/pr1235-cert.pem", "/tmp/pr1235-key.pem")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(5)

        def serve():
            with listener.accept()[0] as conn:
                try:
                    with server_context.wrap_socket(conn, server_side=True) as tls:
                        tls.sendall(b"OK")
                except ssl.SSLError:
                    # Expected for the untrusted-context negative control.
                    pass

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            with socket.create_connection(listener.getsockname(), timeout=5) as conn:
                with client_context.wrap_socket(conn, server_hostname="localhost") as tls:
                    assert tls.recv(2) == b"OK"
        finally:
            thread.join(timeout=6)
            assert not thread.is_alive()


try:
    untrusted_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    untrusted_context.minimum_version = ssl.TLSVersion.TLSv1_2
    handshake(untrusted_context)
except ssl.SSLCertVerificationError:
    print("PASS: untrusted certificates remain rejected", flush=True)
else:
    raise AssertionError("Empty trust store accepted private certificate")
handshake(client_context())
print("PASS: installed private CA works without configuration changes", flush=True)
subprocess.run(["update-ca-certificates", "--fresh"], check=True, stdout=subprocess.DEVNULL)
handshake(client_context())
subprocess.run(["python", "-B", "/trust_test.py", "-v"], check=True)
print("PASS: store refresh preserves private trust and retired-root exclusions", flush=True)
subprocess.run(["python", "/app/scripts/prune_retired_ca.py"], check=True, stdout=subprocess.DEVNULL)
handshake(client_context())
print("PASS: image trust preparation preserves an already-installed private CA", flush=True)
os.environ["SSL_CERT_FILE"] = "/tmp/pr1235-cert.pem"
handshake(client_context())
print("PASS: existing explicit CA file selection", flush=True)
os.environ.pop("SSL_CERT_FILE")
Path("/tmp/pr1235-directory").mkdir()
Path("/tmp/pr1235-directory/root.pem").write_bytes(pem)
subprocess.run(["openssl", "rehash", "/tmp/pr1235-directory"], check=True)
os.environ["SSL_CERT_DIR"] = "/tmp/pr1235-directory"
# Disable the default CA file to prove the explicit directory is sufficient.
os.environ["SSL_CERT_FILE"] = "/tmp/nonexistent-ca.pem"
handshake(client_context())
print("PASS: existing explicit hashed CA directory selection", flush=True)
os.environ.pop("SSL_CERT_FILE")
os.environ.pop("SSL_CERT_DIR")
assert contains(client_context())
