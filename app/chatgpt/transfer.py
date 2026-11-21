"""Owner-operated, encrypted credential handoff to a self-hosted VM (no network calls).

Run locally: python -m app.chatgpt.transfer export --account-id UUID --file PATH
Transfer that encrypted file over SSH, then run import --file PATH on the VM. The
passphrase is read interactively, never from argv/env or printed. Export hands off
this renewable session by clearing local tokens without revoking the remote grant.
"""

import argparse
import asyncio
import base64
import getpass
import json
import os
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.chatgpt.service import ISSUER, ChatGPTService
from app.config import Settings
from app.errors import DomainError


def cipher(passphrase, salt):
    if len(passphrase) < 16:
        raise DomainError("CHATGPT_EXPORT_PASSPHRASE", "Use a passphrase of at least 16 characters")
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=1_200_000).derive(
        passphrase.encode()
    )
    return Fernet(base64.urlsafe_b64encode(key))


async def export_session(service, account_id, path, passphrase):
    path = Path(path)
    async with service.store.refresh_lock(account_id):
        account = service.require_account(account_id)
        salt = os.urandom(16)
        bundle = {
            "version": 1,
            "salt": base64.urlsafe_b64encode(salt).decode(),
            "ciphertext": cipher(passphrase, salt).encrypt(json.dumps(account).encode()).decode(),
        }
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(bundle, output)
            output.flush()
            os.fsync(output.fileno())
        service.clear(account_id, "transferred")


async def import_session(service, path, passphrase):
    path = Path(path)
    service.store._check(path)
    try:
        bundle = json.loads(path.read_text())
        if bundle["version"] != 1:
            raise ValueError("Invalid bundle")
        salt = base64.urlsafe_b64decode(bundle["salt"])
        if len(salt) != 16:
            raise ValueError("Invalid salt")
        data = json.loads(cipher(passphrase, salt).decrypt(bundle["ciphertext"].encode()))
        data["id"] = str(uuid.UUID(data["id"]))
        if (
            data["issuer"] != ISSUER
            or data["client_id"] == "dynamic_agent_client"
            or not isinstance(data["subject"], str)
            or not data["subject"]
            or not isinstance(data["access_token"], str)
            or not data["access_token"]
            or not isinstance(data["refresh_token"], str)
            or not data["refresh_token"]
            or data["status"] != "connected"
        ):
            raise ValueError("Invalid credential record")
    except (InvalidToken, ValueError, KeyError, TypeError) as error:
        raise DomainError(
            "CHATGPT_IMPORT_INVALID", "Protected transfer or passphrase did not validate"
        ) from error
    async with service.store.refresh_lock(data["id"]):
        old = service.store.read("account-" + data["id"])
        if old and (old["client_id"], old["subject"]) != (data["client_id"], data["subject"]):
            raise DomainError(
                "CHATGPT_IMPORT_CONFLICT", "Import would replace another account registration"
            )
        data["ext_agent_host_id"] = service.store.host_id()  # Never overwrite VM host identity.
        service.store.save_account(data)
        service.store.select(data["id"])
    return data["id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["export", "import"])
    parser.add_argument("--file", required=True)
    parser.add_argument("--account-id")
    args = parser.parse_args()
    if args.action == "export" and not args.account_id:
        parser.error("--account-id is required for export")
    phrase = getpass.getpass("Transfer passphrase (16+ characters): ")
    service = ChatGPTService(Settings())
    if args.action == "export":
        if phrase != getpass.getpass("Confirm passphrase: "):
            parser.error("Passphrases do not match")
        asyncio.run(export_session(service, args.account_id, args.file, phrase))
        print(
            "Protected export written; local session handed off. "
            "Keep the file and passphrase private."
        )
    else:
        asyncio.run(import_session(service, args.file, phrase))
        print(
            "Protected session imported. This host now owns refreshes; "
            "remove the transfer file securely."
        )


if __name__ == "__main__":
    main()
