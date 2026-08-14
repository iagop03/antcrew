"""
Google Cloud integration for AntCrew agents.

Provides tool wrappers for BigQuery and Cloud Storage that any
:class:`~antcrew.core.agent.BaseAgent` can use as ``self.tools``.

Install optional dependencies::

    pip install antcrew[google]
    # or: pip install google-cloud-bigquery google-cloud-storage

Usage::

    from antcrew.integrations.google_cloud import BigQueryTool, GCSTool

    class DataAnalystAgent(BaseAgent):
        def __init__(self, llm, project_id: str):
            super().__init__(llm)
            self.tools = [
                BigQueryTool(project_id, dataset_id="analytics"),
                GCSTool("my-results-bucket"),
            ]

        def run(self, state):
            bq = self.tools[0]
            rows = bq.query("SELECT * FROM analytics.events LIMIT 100")
            analysis = self.system("You are a data analyst.", str(rows))
            return {"analysis": analysis}
"""
from __future__ import annotations

from antcrew.core.tools import BaseTool, ToolResult

# ---------------------------------------------------------------------------
# BigQuery
# ---------------------------------------------------------------------------

class BigQueryTool(BaseTool):
    """Execute SQL queries against Google BigQuery and return rows as dicts.

    Args:
        project_id: GCP project ID.
        dataset_id: Default dataset (used for unqualified table names).
        credentials: Optional ``google.oauth2.credentials.Credentials`` — if
                     ``None``, uses Application Default Credentials.

    Requires ``google-cloud-bigquery`` (``pip install antcrew[google]``).
    """

    name = "bigquery"
    description = "Execute a SQL query against Google BigQuery and return rows."

    def __init__(
        self,
        project_id: str,
        dataset_id: str = "",
        *,
        credentials=None,
    ) -> None:
        self._project_id = project_id
        self._dataset_id = dataset_id
        self._credentials = credentials
        self._client = None  # lazily initialised

    def _get_client(self):
        if self._client is None:
            try:
                from google.cloud import bigquery
            except ImportError as exc:
                raise ImportError(
                    "google-cloud-bigquery is required for BigQueryTool.\n"
                    "Install with: pip install antcrew[google]"
                ) from exc
            self._client = bigquery.Client(
                project=self._project_id,
                credentials=self._credentials,
            )
        return self._client

    def query(self, sql: str, *, max_rows: int = 1000) -> list[dict]:
        """Execute *sql* and return up to *max_rows* rows as ``list[dict]``.

        Args:
            sql:      Standard SQL query string.
            max_rows: Safety cap on returned rows (default: 1000).
        """
        client = self._get_client()
        job = client.query(sql)
        return [dict(row) for row in job.result(max_results=max_rows)]

    def __call__(self, sql: str) -> ToolResult:
        """Tool-use interface — called by the agent's ReAct loop."""
        try:
            rows = self.query(sql)
            return ToolResult(output=str(rows[:50]), error=None)
        except Exception as exc:
            return ToolResult(output="", error=str(exc))

    def schema(self) -> str:
        return (
            f"Tool: {self.name}\n"
            f"Description: {self.description}\n"
            "Input: a valid Standard SQL string.\n"
            "Output: list of row dicts (up to 50 rows shown)."
        )


# ---------------------------------------------------------------------------
# Cloud Storage
# ---------------------------------------------------------------------------

class GCSTool(BaseTool):
    """Upload and download text/bytes from Google Cloud Storage.

    Args:
        bucket_name:  GCS bucket name (without ``gs://`` prefix).
        credentials:  Optional ``google.oauth2.credentials.Credentials`` — if
                      ``None``, uses Application Default Credentials.

    Requires ``google-cloud-storage`` (``pip install antcrew[google]``).
    """

    name = "gcs"
    description = "Upload or download text content from Google Cloud Storage."

    def __init__(
        self,
        bucket_name: str,
        *,
        credentials=None,
    ) -> None:
        self._bucket_name = bucket_name
        self._credentials = credentials
        self._bucket = None  # lazily initialised

    def _get_bucket(self):
        if self._bucket is None:
            try:
                from google.cloud import storage
            except ImportError as exc:
                raise ImportError(
                    "google-cloud-storage is required for GCSTool.\n"
                    "Install with: pip install antcrew[google]"
                ) from exc
            client = storage.Client(credentials=self._credentials)
            self._bucket = client.bucket(self._bucket_name)
        return self._bucket

    def upload(self, content: "str | bytes", gcs_path: str, *, content_type: str = "text/plain") -> str:
        """Upload *content* to ``gs://{bucket}/{gcs_path}``.

        Returns:
            The full GCS URI ``gs://{bucket}/{gcs_path}``.
        """
        bucket = self._get_bucket()
        blob = bucket.blob(gcs_path)
        if isinstance(content, str):
            blob.upload_from_string(content.encode(), content_type=content_type)
        else:
            blob.upload_from_string(content, content_type=content_type)
        return f"gs://{self._bucket_name}/{gcs_path}"

    def download(self, gcs_path: str, *, encoding: str = "utf-8") -> str:
        """Download ``gs://{bucket}/{gcs_path}`` and return as a string."""
        bucket = self._get_bucket()
        blob = bucket.blob(gcs_path)
        return blob.download_as_bytes().decode(encoding)

    def upload_artifact(self, artifact, gcs_path: str) -> str:
        """Serialise a Pydantic artifact to JSON and upload it.

        Args:
            artifact: Any Pydantic ``BaseModel`` instance.
            gcs_path: Destination path inside the bucket.

        Returns:
            The full GCS URI.
        """
        content = artifact.model_dump_json(indent=2)
        return self.upload(content, gcs_path, content_type="application/json")

    def __call__(self, instruction: str) -> ToolResult:
        """Tool-use interface — expects ``upload:<path>:<content>`` or ``download:<path>``."""
        try:
            if instruction.startswith("download:"):
                path = instruction[len("download:"):].strip()
                content = self.download(path)
                return ToolResult(output=content[:2000], error=None)
            elif instruction.startswith("upload:"):
                _, path, *parts = instruction.split(":", 2)
                content = ":".join(parts)
                uri = self.upload(content.strip(), path.strip())
                return ToolResult(output=f"Uploaded to {uri}", error=None)
            else:
                return ToolResult(output="", error="Unknown instruction. Use 'upload:<path>:<content>' or 'download:<path>'.")
        except Exception as exc:
            return ToolResult(output="", error=str(exc))

    def schema(self) -> str:
        return (
            f"Tool: {self.name}\n"
            f"Description: {self.description}\n"
            "Input: 'upload:<path>:<content>' to upload, or 'download:<path>' to read.\n"
            "Output: uploaded GCS URI or downloaded content."
        )


# ---------------------------------------------------------------------------
# VertexAI model helper
# ---------------------------------------------------------------------------

def gemini_via_vertex(
    model: str = "gemini-2.0-flash",
    *,
    project_id: str,
    region: str = "us-central1",
):
    """Return a :class:`~antcrew.models.gemini_model.GeminiModel` backed by Vertex AI.

    Requires ``google-cloud-aiplatform`` and that ``GOOGLE_APPLICATION_CREDENTIALS``
    is set (or running on a GCP VM with a service account).

    Args:
        model:      Gemini model name (e.g. ``"gemini-2.0-flash"``).
        project_id: GCP project ID.
        region:     Vertex AI region (default: ``"us-central1"``).

    Example::

        from antcrew.integrations.google_cloud import gemini_via_vertex
        llm = gemini_via_vertex("gemini-2.0-flash", project_id="my-project")
        team = QuickStart.dev(llm)
    """
    try:
        import vertexai  # type: ignore[import]
    except ImportError as exc:
        raise ImportError(
            "google-cloud-aiplatform is required for Vertex AI.\n"
            "Install with: pip install google-cloud-aiplatform"
        ) from exc

    vertexai.init(project=project_id, location=region)
    from antcrew.models.gemini_model import GeminiModel
    return GeminiModel(model, use_vertex=True)
