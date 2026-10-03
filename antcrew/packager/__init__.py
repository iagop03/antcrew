"""ChangePackager — produces Excel change packages, summaries, and approval emails.

Usage::

    from antcrew.packager import ChangePackager
    from antcrew.models.release import Release

    packager = ChangePackager(release, trace_log=tlog)
    result = packager.build(output_dir="./change-package")
    # result.excel_path, result.summary_md, result.email_drafts
"""
from antcrew.packager.change_packager import ChangePackager, PackageResult

__all__ = ["ChangePackager", "PackageResult"]
