"""antcrew.augment.cobol — add AI to COBOL programs without rewriting them."""
from .analyzer import COBOLAnalyzer, COBOLAnalysis
from .ai_generator import AIGenerator
from .integrator import Integrator


class COBOLAugment:
    """High-level facade: analyse COBOL, generate Python AI wrapper + integration.

    Example::

        aug = COBOLAugment(llm=my_llm)
        result = aug.augment("ORDPRC.cbl", requirement="Add ML fraud scoring")
        print(result.python_wrapper)
        print(result.cobol_caller)
        print(result.deployment_guide)
    """

    def __init__(self, llm: object | None = None) -> None:
        self._llm = llm
        self.analyzer = COBOLAnalyzer()
        self.generator = AIGenerator(llm=llm)
        self.integrator = Integrator()

    def augment(self, cobol_file: str, requirement: str) -> "AugmentResult":
        analysis = self.analyzer.analyze(cobol_file)
        python_wrapper = self.generator.generate(analysis, requirement)
        cobol_caller = self.integrator.generate_caller(analysis)
        deployment_guide = self.integrator.generate_guide(analysis, python_wrapper)
        return AugmentResult(
            analysis=analysis,
            python_wrapper=python_wrapper,
            cobol_caller=cobol_caller,
            deployment_guide=deployment_guide,
        )


class AugmentResult:
    def __init__(
        self,
        analysis: "COBOLAnalysis",
        python_wrapper: str,
        cobol_caller: str,
        deployment_guide: str,
    ) -> None:
        self.analysis = analysis
        self.python_wrapper = python_wrapper
        self.cobol_caller = cobol_caller
        self.deployment_guide = deployment_guide

    def __repr__(self) -> str:
        return (
            f"AugmentResult(program={self.analysis.program_id!r}, "
            f"paragraphs={len(self.analysis.paragraphs)}, "
            f"data_items={len(self.analysis.data_items)})"
        )


__all__ = ["COBOLAugment", "AugmentResult", "COBOLAnalyzer", "COBOLAnalysis", "AIGenerator", "Integrator"]
