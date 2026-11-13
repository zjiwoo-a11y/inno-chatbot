from abc import ABC, abstractmethod
from typing import Iterator

from app.config import settings

SYSTEM_PROMPT = """당신은 사내 프로젝트 수행을 돕는 어시스턴트입니다.
반드시 아래 <문서> 안의 내용만 근거로 답하세요.
- 문서에 없는 내용은 절대 지어내지 말고 "제공된 문서에서 답을 찾을 수 없습니다"라고만 답하세요.
- 하나의 청크에 완벽한 정답이 없더라도, 여러 청크에 흩어진 관련 정보를 종합해서 답할 수 있으면
  적극적으로 종합해서 답하세요. 질문과 관련된 내용이 문서에 조금이라도 있다면, 모른다고 하기보다는
  그 내용을 바탕으로 최대한 답하려고 시도하세요. 문서에 전혀 관련 내용이 없을 때만 "찾을 수 없습니다"라고 하세요.
- 답변 마지막 줄에 실제로 근거로 사용한 청크 번호를 다음 형식으로 반드시 붙이세요: [CITATIONS: 1,2]
- 근거가 전혀 없으면 [CITATIONS: NONE] 이라고만 쓰세요.
"""


class LLMClient(ABC):
    @abstractmethod
    def generate_stream(self, question: str, context_blocks: list[str]) -> Iterator[str]:
        ...


class OpenAILLMClient(LLMClient):
    def __init__(self):
        from openai import OpenAI
        self._client = OpenAI(api_key=settings.OPENAI_API_KEY)

    def _build_prompt(self, question: str, context_blocks: list[str]) -> str:
        doc_section = "\n\n".join(
            f"[{i+1}] {block}" for i, block in enumerate(context_blocks)
        )
        return f"<문서>\n{doc_section}\n</문서>\n\n질문: {question}"

    def generate_stream(self, question: str, context_blocks: list[str]) -> Iterator[str]:
        prompt = self._build_prompt(question, context_blocks)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        models_to_try = [settings.OPENAI_CHAT_MODEL, settings.OPENAI_FALLBACK_MODEL]
        last_error = None
        for model in models_to_try:
            try:
                stream = self._client.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                )
                for chunk in stream:
                    delta = chunk.choices[0].delta.content if chunk.choices else None
                    if delta:
                        yield delta
                return
            except Exception as e:
                last_error = e
                continue
        raise RuntimeError(f"모든 OpenAI 모델 호출 실패: {last_error}")


def get_llm_client() -> LLMClient:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY가 설정되지 않았습니다. .env 파일을 확인하세요."
        )
    return OpenAILLMClient()


def extract_citations(full_answer: str) -> tuple[str, list[int]]:
    import re

    m = re.search(r"\[CITATIONS:\s*(NONE|[\d,\s]+)\]", full_answer)
    if not m:
        return full_answer.strip(), []
    tag = m.group(1).strip()
    body = full_answer[: m.start()].strip()
    if tag == "NONE":
        return body, []
    ids = [int(x.strip()) for x in tag.split(",") if x.strip().isdigit()]
    return body, ids
