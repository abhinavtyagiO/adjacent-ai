import ipaddress
from urllib.parse import urlsplit, urlunsplit


def public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


def normalize_url(value: str) -> str:
    """Require HTTP(S), standard ports and a public-looking host; discard query/fragment."""
    if len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ValueError("Invalid website URL")
    try:
        url = urlsplit(value)
        if url.scheme.lower() not in {"http", "https"} or not url.hostname:
            raise ValueError("An absolute http(s) website URL is required")
        if url.username or url.password or url.port not in {None, 80, 443}:
            raise ValueError("Credentials and non-standard ports are not allowed")
        host = url.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith((".local", ".localhost", ".internal")):
                raise ValueError("A public company website is required") from None
        else:
            if not public_ip(host):
                raise ValueError("Private and reserved IP addresses are not allowed")
        if ":" in host:
            host = f"[{host}]"
        # HTTP:443 and HTTPS:80 are unusual and intentionally rejected.
        if url.port and url.port != (443 if url.scheme.lower() == "https" else 80):
            raise ValueError("The port must match the HTTP(S) scheme")
        return urlunsplit((url.scheme.lower(), host, url.path or "/", "", ""))
    except (UnicodeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
