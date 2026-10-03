"""Impact analysis module — determines what a set of VCS changes affects.

Usage::

    from antcrew.augment.impact import ImpactAnalyzer, ImpactAnalysis
    from antcrew.augment.cobol.index import COBOLIndex

    idx = COBOLIndex("~/.antcrew/cobol.db")
    analyzer = ImpactAnalyzer(idx)
    analysis = analyzer.analyze(
        changed_files=["src/ACCTUPD.cbl", "src/MAINPGM.cbl"],
        change_ref="CR-1234",
    )
    print(analysis.risk_level)          # "high"
    print(analysis.affected_callers)    # ["BATCHCTL", "NIGHTLY"]
"""
from antcrew.augment.impact.models import ImpactAnalysis, ImpactedComponent
from antcrew.augment.impact.analyzer import ImpactAnalyzer

__all__ = ["ImpactAnalysis", "ImpactedComponent", "ImpactAnalyzer"]
