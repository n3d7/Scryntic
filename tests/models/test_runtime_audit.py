import pytest

from scripts.audit import advisory_requirements, expected_packages, validate_report

CPU_HASH = "a09987c95ec4cffdb6df798d3d641558110a334cfccce22f2f046d83142bc260"


def test_reviewed_cpu_build_is_mapped_without_skipping_advisories() -> None:
    source = f"torch==2.14.0+cpu \\\n    --hash=sha256:{CPU_HASH}\n"
    lookup = advisory_requirements(source)
    assert "torch==2.14.0+cpu" in source
    assert expected_packages(lookup) == {("torch", "2.14.0")}
    assert CPU_HASH in lookup
    expected = expected_packages(lookup)
    with pytest.raises(ValueError):
        validate_report(
            {
                "dependencies": [
                    {"name": "torch", "version": "2.14.0", "vulns": [{"id": "example"}]}
                ]
            },
            expected,
        )


def test_unreviewed_local_build_is_not_normalized() -> None:
    with pytest.raises(ValueError):
        advisory_requirements("torch==2.14.1+cpu --hash=sha256:" + CPU_HASH)
    with pytest.raises(ValueError):
        advisory_requirements("torch==2.14.0+cpu --hash=sha256:" + "0" * 64)
