import logging
import json
from langchain_core.callbacks import BaseCallbackHandler

log = logging.getLogger(__name__)

class DumpPayloadCallback(BaseCallbackHandler):
    def on_llm_start(self, serialized, prompts, **kwargs):
        log.info("=== LLM START ===")
        log.info("serialized=%s", json.dumps(serialized, default=str)[:2000])
        log.info("prompts=%s", prompts)

    def on_chat_model_start(self, serialized, messages, **kwargs):
        log.info("=== CHAT START ===")
        log.info("messages=%s", messages)
        log.info("invocation_params=%s", kwargs.get("invocation_params"))
        log.info("options=%s", kwargs.get("options"))
        log.info("metadata=%s", kwargs.get("metadata"))