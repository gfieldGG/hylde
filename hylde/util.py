import hashlib


def md5(s: str) -> str:
    md5_hash = hashlib.md5()
    md5_hash.update(s.encode("utf-8"))
    return md5_hash.hexdigest()
