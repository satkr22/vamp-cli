from langchain_openai import ChatOpenAI


class ModelFactory:
    def __init__(
        self,
        base_url: str,
        api_key: str,
    ) -> None:
        self.base_url = base_url
        self.api_key = api_key

    def create(
        self,
        model: str,
        temperature: float = 0.0,
    ) -> ChatOpenAI:

        return ChatOpenAI(
            model=model,
            base_url=self.base_url,
            api_key=self.api_key, # type: ignore
            temperature=temperature,
        )