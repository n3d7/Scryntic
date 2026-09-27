# Source package

This package contains the Scryntic application, domain model, and implementation adapters. Each subdirectory owns one architectural area.

For the F09 local demonstration, run `python -m scryntic --home <prepared-home> --epoch <delivery-id>` from the repository root. The CLI composes the durable ingestion-to-forecast slice using a trusted fake source and one of two fake providers (`--provider persistence` or `--provider trend`). The home must already have the private workstation directories required by `Installation`.
