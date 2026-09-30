"""Independent coordinator checks on hostile manifests and worker output."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scryntic.imports.protocol import (
    ImportError,
    ImportLimits,
    parse_request,
    result_bytes,
    validate_result,
)
from scryntic.publication.coordinator import Published
from scryntic.publication.manifest import ManifestStorage, prepare_manifest
from tests.publication.helpers import configured_coordinator


def test_result_is_exact_and_canonical(tmp_path: Path) -> None:
    bundle = configured_coordinator(tmp_path)
    try:
        published = bundle.coordinator.publish_next()
        assert isinstance(published, Published)
        data = ManifestStorage(bundle.root).read_exact(published.manifest, 65536)
        request = parse_request(data, ImportLimits())
        good = result_bytes(request)
        validate_result(good, request)
        value = json.loads(good)
        for field, changed in (
            ("protocol", True),
            ("manifest_hash", "0" * 64),
            ("status", "error"),
        ):
            bad_mapping = dict(value, **{field: changed})
            with pytest.raises(ImportError):
                validate_result(json.dumps(bad_mapping).encode(), request)
        for bad in (
            good + b"\n",
            b'{"protocol":1,"protocol":1}',
            good + b"x",
            b"[]",
            b"x" * 65537,
        ):
            with pytest.raises(ImportError):
                validate_result(bad, request)
        raw = request.body.objects[0]
        unsupported = replace(raw, codec="remote-codec")
        body = replace(request.body, objects=(unsupported, request.body.objects[1]))
        with pytest.raises(ImportError):
            parse_request(prepare_manifest(body), ImportLimits())
    finally:
        bundle.close()
