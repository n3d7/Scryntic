"""Deterministic F11 boundaries shared by exception and process-exit probes."""

OBJECT_BOUNDARIES = (
    "before_archive_directory_fsync",
    "after_archive_directory_fsync",
    "before_object_file_fsync",
    "after_object_file_fsync",
    "before_object_readonly_fsync",
    "after_object_readonly_fsync",
    "before_object_install",
    "after_object_install",
    "before_object_directory_fsync",
    "after_object_directory_fsync",
    "before_staging_directory_fsync",
    "after_staging_directory_fsync",
)
VIEW_BOUNDARIES = (
    "before_archive_directory_fsync",
    "after_archive_directory_fsync",
    "before_partition_install",
    "after_partition_install",
    "before_partition_directory_fsync",
    "after_partition_directory_fsync",
)
MANIFEST_BOUNDARIES = tuple(
    f"{side}_manifest_{operation}"
    for operation in (
        "parent_directory_fsync",
        "file_fsync",
        "readonly_fsync",
        "install",
        "directory_fsync",
        "staging_directory_fsync",
    )
    for side in ("before", "after")
)
TRANSACTION_BOUNDARIES = (
    "before_reserve_commit",
    "after_reservation_commit",
    "before_prepare_commit",
    "after_prepare_commit",
    "after_catalog_insert",
    "after_checkpoint_update",
    "before_catalog_commit",
    "after_catalog_commit",
)
BOUNDARIES = (
    *(
        f"{role}_{stage}"
        for role in ("raw", "normalized")
        for stage in OBJECT_BOUNDARIES
    ),
    *(f"views_{stage}" for stage in VIEW_BOUNDARIES),
    *MANIFEST_BOUNDARIES,
    *TRANSACTION_BOUNDARIES,
    "after_raw_object",
    "after_normalized_object",
    "after_partition_views",
)
