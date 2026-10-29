"""Unknown or empty native output must never become a silent successful reply."""
import pytest
from google.genai import types

from src.kernel.llm.exceptions import LLMAPIError
from src.kernel.llm.model_client.gemini_codec import decode_response


@pytest.mark.parametrize("part", [types.Part(inline_data=types.Blob(data=b"image",mime_type="image/png")),types.Part(executable_code=types.ExecutableCode(code="print(1)",language="PYTHON"))])
def test_unsupported_native_output_is_rejected(part: types.Part) -> None:
    """The Chat adapter cannot claim to have returned media or executed code."""
    response=types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model",parts=[part]),finish_reason="STOP")])
    with pytest.raises(LLMAPIError):
        decode_response(response,("p","https://offline.invalid/v1beta","test"))


def test_empty_completed_output_is_rejected() -> None:
    """A signed empty tail is valid only alongside actual response content."""
    response=types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model",parts=[]),finish_reason="STOP")])
    with pytest.raises(LLMAPIError):decode_response(response,("p","https://offline.invalid/v1beta","test"))
