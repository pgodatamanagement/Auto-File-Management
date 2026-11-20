from pathlib import Path

import pytest

import organize_folder as of


def make(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def snapshot(root: Path) -> dict[str, str]:
    """Every file under root (relative path -> content)."""
    return {str(p.relative_to(root)): p.read_text() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("name, expected", [
    ("a.jpg", "Images"), ("a.JPG", "Images"), ("a.mp4", "Videos"),
    ("a.mp3", "Audio"), ("a.pdf", "Documents"), ("a.zip", "Documents"),
    ("a.xyz", "Others"), ("README", "Others"), (".gitignore", "Others"),
    ("archive.tar.gz", "Others"),
])
def test_category_for(name, expected):
    assert of.category_for(Path(name)) == expected


def test_no_extension_overlaps_between_categories():
    seen = {}
    for category, exts in of.EXTENSIONS_BY_CATEGORY.items():
        for ext in exts:
            assert ext not in seen, f"{ext} in {seen[ext]} and {category}"
            seen[ext] = category


def test_moves_files_and_creates_folders(tmp_path):
    make(tmp_path / "a.jpg")
    make(tmp_path / "b.mp3")
    result = of.organize(tmp_path)
    assert result == of.Result(moved=2, failed=0)
    assert (tmp_path / "Images" / "a.jpg").exists()
    assert (tmp_path / "Audio" / "b.mp3").exists()
    for name in of.CATEGORY_DIRS:
        assert (tmp_path / name).is_dir()


def test_never_overwrites_on_name_collision(tmp_path):
    make(tmp_path / "Images" / "a.jpg", "old")
    make(tmp_path / "a.jpg", "new")
    of.organize(tmp_path)
    assert (tmp_path / "Images" / "a.jpg").read_text() == "old"
    assert (tmp_path / "Images" / "a (1).jpg").read_text() == "new"


def test_nothing_is_deleted(tmp_path):
    for name in ["a.jpg", "b.pdf", "c.txt", "d", "e.mp4"]:
        make(tmp_path / name, name)
    before = sorted(snapshot(tmp_path).values())
    of.organize(tmp_path)
    assert sorted(snapshot(tmp_path).values()) == before


def test_existing_subfolders_are_not_entered_or_moved(tmp_path):
    make(tmp_path / "Vacation" / "pic.jpg")
    of.organize(tmp_path)
    assert (tmp_path / "Vacation" / "pic.jpg").exists()
    assert not (tmp_path / "Images" / "pic.jpg").exists()


def test_is_idempotent(tmp_path):
    make(tmp_path / "a.jpg")
    of.organize(tmp_path)
    first = snapshot(tmp_path)
    assert of.organize(tmp_path) == of.Result(0, 0)
    assert snapshot(tmp_path) == first


def test_dry_run_changes_nothing(tmp_path):
    make(tmp_path / "a.jpg")
    before = snapshot(tmp_path)
    result = of.organize(tmp_path, dry_run=True)
    assert result.moved == 1
    assert snapshot(tmp_path) == before
    assert not (tmp_path / "Images").exists()


def test_missing_directory_raises(tmp_path):
    with pytest.raises(NotADirectoryError):
        of.organize(tmp_path / "nope")


def test_one_failure_does_not_abort_the_rest(tmp_path, monkeypatch):
    make(tmp_path / "bad.jpg")
    make(tmp_path / "good.jpg")
    real_rename = Path.rename

    def flaky(self, target):
        if self.name == "bad.jpg":
            raise PermissionError("locked")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    result = of.organize(tmp_path)
    assert result == of.Result(moved=1, failed=1)
    assert (tmp_path / "bad.jpg").exists()           # left in place, not lost
    assert (tmp_path / "Images" / "good.jpg").exists()


def test_category_folder_symlinked_outside_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    make(root / "a.jpg")
    try:
        (root / "Images").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted on this system")
    with pytest.raises(OSError):
        of.organize(root)
    assert (root / "a.jpg").exists()
    assert list(outside.iterdir()) == []


def test_symlinked_files_are_left_alone(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    target = make(tmp_path / "elsewhere.jpg")
    try:
        (root / "link.jpg").symlink_to(target)
    except OSError:
        pytest.skip("symlinks not permitted on this system")
    of.organize(root)
    assert (root / "link.jpg").is_symlink()
