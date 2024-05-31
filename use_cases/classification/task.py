from typing import Dict, Any
from dataclasses import dataclass, field

import utils.setup_env
import re

import logging

from core.component import Component, Sequential, fun_to_component
from core.generator import Generator
from components.api_client import (
    GroqAPIClient,
    OpenAIClient,
    GoogleGenAIClient,
    AnthropicAPIClient,
)
from core.prompt_builder import Prompt
from prompts.outputs import YAMLOutputParser
from core.string_parser import JsonParser

from use_cases.classification.data import (
    _COARSE_LABELS,
    _COARSE_LABELS_DESC,
)

logger = logging.getLogger(__name__)

CLASSIFICATION_TASK_DESC = r"""You are a classifier. Given a Question, you need to classify it into one of the following classes:
Format: class_index. class_name, class_description
{% for class in classes %}
{{loop.index-1}}. {{class.label}}, {{class.desc}}
{% endfor %}
"""

TEMPLATE = r"""{# task desc #}
{% if task_desc_str %}
{{task_desc_str}}
{% endif %}
{%if output_format_str %}
<OUTPUT_FORMAT>
{{output_format_str}}
</OUTPUT_FORMAT>
{% endif %}
{# example #}
{% if examples_str %}
<EXAMPLES>
{#{% for example in examples_str %}#}
{{examples_str}}
{#{% endfor %}#}
</EXAMPLES>
{% endif %}
{{input_label}}: {{input}}
Your output:
"""

from core.data_classes import BaseDataClass
from use_cases.classification.data import _COARSE_LABELS_DESC, _COARSE_LABELS


@dataclass
class InputFormat(BaseDataClass):
    # add the "prompt_arg" to represent the prompt argument that it should get matched to
    question: str = field(metadata={"desc": "The question to classify"})

    @classmethod
    def load_from_dict(cls, data: Dict[str, Any]):
        # customize to convert data item from a dataset into input data object
        # "text" -> "question"
        data = {"question": data["text"]}
        return super().load_from_dict(data)


@dataclass
class OutputFormat(BaseDataClass):
    thought: str = field(
        metadata={
            "desc": "Your reasoning to classify the question to class_name",
        }
    )
    class_name: str = field(metadata={"desc": "class_name"})

    class_index: int = field(metadata={"desc": "class_index in range[0, 5]"})

    @classmethod
    def load_from_dict(cls, data: Dict[str, Any]):
        # customize to convert data item from a dataset into output data object
        # "label" -> "class_index"
        data = {
            "thought": None,
            "class_index": data["coarse_label"],
            "class_name": _COARSE_LABELS_DESC[data["coarse_label"]],
        }
        return super().load_from_dict(data)


class TRECClassifier(Component):
    r"""
    Optimizing goal is the examples_str in the prompt
    """

    def __init__(
        self, labels: list = _COARSE_LABELS, labels_desc: list = _COARSE_LABELS_DESC
    ):
        super().__init__()
        self.labels = labels
        self.num_classes = len(labels)
        self.labels_desc = labels_desc
        labels_desc = [
            {"label": label, "desc": desc} for label, desc in zip(labels, labels_desc)
        ]
        # custome prompt with variable, use Prompt to generate it
        # the varaibles in the prompts become the model parameters to optimize
        # component and variables

        self.task_desc_prompt = Prompt(
            template=CLASSIFICATION_TASK_DESC,
            preset_prompt_kwargs={"classes": labels_desc},
        )
        self.task_desc_str = self.task_desc_prompt()

        # self.parameters = [
        #     {
        #         "component": Generator,
        #         "args": {
        #             "model_client": GroqAPIClient,
        #             "model_kwargs": {"model": "llama3-8b-8192", "temperature": 0.0},
        #             "preset_prompt_kwargs": {
        #                 "task_desc_str": self.task_desc_str,
        #                 # "output_format_str": OUTPUT_FORMAT_STR,
        #             },
        #         },
        #     }
        # ]
        yaml_parser = YAMLOutputParser(
            data_class=OutputFormat,  # example=output_example
        )
        # output_str = OutputFormat.to_json_signature()
        output_str = yaml_parser.format_instructions()
        logger.debug(f"output_str: {output_str}")
        groq_model_kwargs = {
            "model": "llama3-8b-8192",  # "llama3-8b-8192",  # "llama3-8b-8192",  # "llama3-8b-8192", #gemma-7b-it not good at following yaml format
            "temperature": 0.0,
            "top_p": 1,
            "frequency_penalty": 0,
            "presence_penalty": 0,
            "n": 1,
        }
        openai_model_kwargs = {
            "model": "gpt-3.5-turbo",
            "temperature": 0.0,
            "top_p": 1,
            "frequency_penalty": 0,
            "presence_penalty": 0,
            "n": 1,
        }
        google_model_kwargs = {
            "model": "gemini-1.5-pro-latest",
            "temperature": 0.0,
            "top_p": 1,
            # "frequency_penalty": 0,
            # "presence_penalty": 0,
            # "n": 1,
        }
        anthropic_model_kwargs = {
            "model": "claude-3-opus-20240229",
            "temperature": 0.0,
            "top_p": 1,
            # "frequency_penalty": 0,
            # "presence_penalty": 0,
            # "n": 1,
            "max_tokens": 1024,
        }

        def format_class_label(x: Dict[str, Any]) -> int:
            label = int(x["class_index"])
            if label >= self.num_classes:
                label = -1
            return label

        self.generator = Generator(
            model_client=GroqAPIClient(),
            model_kwargs=groq_model_kwargs,
            template=TEMPLATE,
            preset_prompt_kwargs={
                "task_desc_str": self.task_desc_str,
                "output_format_str": output_str,
                "input_label": "Question",
            },
            trainable_params=["examples_str", "task_desc_str"],
            output_processors=Sequential(
                yaml_parser, fun_to_component(format_class_label)
            ),
        )

    # def init_parameters(self):
    #     self.generator.examples_str.update_value()

    def call(self, query: str) -> str:
        str_response: Dict[str, Any] = self.generator.call(
            prompt_kwargs={"input": query}
        )

        # use re to find the first integer in the response, can be multiple digits
        re_pattern = r"\d+"
        # label = re.findall(re_pattern, str_response)
        # if label:
        #     label = int(label[0])
        # else:
        #     label = -1
        # if label >= self.num_classes:
        #     label = -1

        # class_name = self.labels[label]

        label = str_response
        if isinstance(label, str):
            label_match = re.findall(re_pattern, label)
            if label_match:
                label = int(label_match[0])
            else:
                label = -1
        return label


if __name__ == "__main__":
    # test one example
    query = "How did serfdom develop in and then leave Russia ?"
    trec_classifier = TRECClassifier(labels=_COARSE_LABELS)
    print(trec_classifier)
    trec_classifier.generator.print_prompt()
    label = trec_classifier.call(query)
    print(f"label: {label}")
