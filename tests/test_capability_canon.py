from brain.capability_canon import canonicalize, aliases_are_same_authority, canonicalize_set


def test_fs_read_aliases():
    assert canonicalize("fs.read") == "filesystem.read"
    assert canonicalize("ucip:filesystem.read") == "filesystem.read"
    assert aliases_are_same_authority("fs.read", "ucip:filesystem.read")


def test_alias_does_not_expand():
    a = canonicalize("fs.read")
    b = canonicalize("ucip:filesystem.read")
    assert a == b
    # write is different capability
    assert canonicalize("fs.write") != a


def test_shell_aliases():
    assert canonicalize("shell.exec") == "shell.execute"
    assert canonicalize("ucip:execution.bash") == "shell.execute"


def test_set_normalize():
    s = canonicalize_set(["fs.read", "ucip:filesystem.read", "fs.write"])
    assert s == {"filesystem.read", "filesystem.write"}
