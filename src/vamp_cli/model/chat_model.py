from langchain_openai import ChatOpenAI


class ChatModel(ChatOpenAI):

    def __init__(
        self,
        model: str,
        base_url: str = "<url>",
        api_key: str = "<api-key>",
        temperature: float = 0.0,
    ) -> None:
        super().__init__(
            model=model,
            base_url=base_url,
            api_key=api_key, # type: ignore
            temperature=temperature,
        )