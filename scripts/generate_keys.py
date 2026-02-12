"""
Generate RSA-2048 keypair for JWT RS256 token signing.

Creates keys/private.pem and keys/public.pem.
Run once during project setup: python scripts/generate_keys.py
"""

import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_keys(output_dir: str = "keys") -> None:
    """Generate an RSA-2048 keypair and write PEM files."""
    keys_dir = Path(output_dir)
    keys_dir.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Write private key
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_path = keys_dir / "private.pem"
    private_path.write_bytes(private_pem)

    # Write public key
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    public_path = keys_dir / "public.pem"
    public_path.write_bytes(public_pem)

    print(f"RSA keypair generated:")
    print(f"  Private key: {private_path.resolve()}")
    print(f"  Public key:  {public_path.resolve()}")


if __name__ == "__main__":
    # Run from project root
    project_root = Path(__file__).resolve().parent.parent
    os.chdir(project_root)
    generate_keys()
