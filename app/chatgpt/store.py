"""Encrypted local credentials, atomic writes and cross-process refresh serialization.

The API and worker share an owner-only directory/volume. The encryption key lives in
that protected directory; this protects backups/casual file disclosure, not a compromised
runtime that can read both files. Never expose tokens through API serializers or logs.
"""

import asyncio
import fcntl
import json
import os
import re
import stat
import tempfile
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.errors import DomainError


class CredentialStore:
    def __init__(self, directory):
        self.root = Path(directory).absolute()
        if self.root.is_symlink():
            raise DomainError("CHATGPT_STORAGE_UNSAFE", "Credential directory cannot be a symlink")
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.root.stat().st_uid != os.getuid():
            raise DomainError(
                "CHATGPT_STORAGE_UNSAFE", "Credential directory must belong to this user"
            )
        self.root.chmod(0o700)
        with self.lock("initialize"):
            key_path = self.root / "master.key"
            if not key_path.exists():
                self._atomic(key_path, Fernet.generate_key())
            self._check(key_path)
            try:
                self.cipher = Fernet(key_path.read_bytes())
            except (ValueError, TypeError) as error:
                raise DomainError(
                    "CHATGPT_STORAGE_INVALID", "Credential encryption key is invalid"
                ) from error

    def _path(self, name):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,150}", name):
            raise DomainError("CHATGPT_STORAGE_INVALID", "Invalid credential record identifier")
        return self.root / (name + ".enc")

    @staticmethod
    def _check(path):
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise DomainError(
                "CHATGPT_STORAGE_UNSAFE", "Credential files require owner-only permissions"
            )

    def _atomic(self, path, data):
        descriptor, temporary = tempfile.mkstemp(dir=self.root, prefix=".write-")
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextmanager
    def lock(self, name):
        path = self.root / (name + ".lock")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            self._check(path)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    @asynccontextmanager
    async def refresh_lock(self, account_id, timeout=30):
        """Hold only this session's lock across renewal, without blocking the event loop."""
        path = self.root / ("refresh-" + str(uuid.UUID(str(account_id))) + ".lock")
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        started = time.monotonic()
        try:
            self._check(path)
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() - started > timeout:
                        raise DomainError(
                            "CHATGPT_REFRESH_BUSY", "Account renewal is busy; try later", 503
                        ) from None
                    await asyncio.sleep(0.05)
            yield
        finally:
            os.close(descriptor)

    def read(self, name):
        path = self._path(name)
        if not path.exists():
            return None
        self._check(path)
        try:
            return json.loads(self.cipher.decrypt(path.read_bytes()))
        except (InvalidToken, ValueError) as error:
            raise DomainError(
                "CHATGPT_STORAGE_INVALID", "Protected credential record is unreadable"
            ) from error

    def write(self, name, value):
        self._atomic(self._path(name), self.cipher.encrypt(json.dumps(value).encode()))

    def host_id(self):
        with self.lock("host"):
            host = self.read("host")
            if not host:
                host = {"id": "urn:uuid:" + str(uuid.uuid4())}
                self.write("host", host)
            return host["id"]

    def account(self, account_id):
        value = self.read("account-" + str(uuid.UUID(str(account_id))))
        if not value:
            raise DomainError("CHATGPT_ACCOUNT_NOT_FOUND", "ChatGPT account was not found", 404)
        return value

    def save_account(self, value):
        self.write("account-" + str(uuid.UUID(value["id"])), value)

    def accounts(self):
        return [self.read(path.stem) for path in sorted(self.root.glob("account-*.enc"))]

    def select(self, account_id):
        self.account(account_id)
        with self.lock("active"):
            self.write("active", {"id": str(account_id)})

    def selected_id(self):
        active = self.read("active")
        return active["id"] if active else None

    def consume_pending(self, name, cookie_hash):
        with self.lock("pending"):
            pending = self.read(name)
            import secrets

            if (
                not pending
                or pending["expires_at"] < time.time()
                or not secrets.compare_digest(pending["cookie_hash"], cookie_hash)
            ):
                raise DomainError(
                    "CHATGPT_OAUTH_STATE_INVALID",
                    "Sign-in expired or browser state did not match",
                    400,
                )
            self._path(name).unlink()
            return pending
