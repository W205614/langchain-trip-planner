"""Checksum guard shared by Java backup restoration checks."""
import hashlib


def validate_archive(content, expected):
    if hashlib.sha256(content).hexdigest() != expected:
        raise ValueError("Archive checksum mismatch; refusing restoration")
