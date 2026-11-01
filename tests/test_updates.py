import subprocess
from pdfreader import updates


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                   cwd=cwd, check=True, capture_output=True)


def test_release_tags_compare_numerically():
    assert updates.is_newer("v0.10.0", "0.9.1")
    assert not updates.is_newer("v0.8.0", "0.8.0")
    assert not updates.is_newer("nightly", "0.8.0")


def test_source_checkout_detects_and_fast_forwards_new_commits(tmp_path):
    # A local bare repository stands in for GitHub; no network is used.
    remote, app, other = tmp_path / "remote.git", tmp_path / "app", tmp_path / "other"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(other))
    (other / "a.txt").write_text("1")
    git(other, "add", "a.txt")
    git(other, "commit", "-qm", "first")
    git(other, "push", "-q", "origin", "HEAD:main")
    git(tmp_path, "clone", "-q", str(remote), str(app))
    assert updates.check(app) is None
    (other / "a.txt").write_text("2")
    git(other, "commit", "-qam", "second")
    git(other, "push", "-q", "origin", "HEAD:main")
    assert updates.check(app) == "GitHub 有 1 个新提交"
    updates.apply(app)
    assert (app / "a.txt").read_text() == "2"
    assert updates.check(app) is None
