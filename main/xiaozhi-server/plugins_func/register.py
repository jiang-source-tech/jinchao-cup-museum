from enum import Enum


class Action(Enum):
    ERROR = -1
    NOTFOUND = 0
    NONE = 1
    RESPONSE = 2
    REQLLM = 3
    RECORD = 4


class ActionResponse:
    def __init__(self, action: Action, result=None, response=None):
        self.action = action
        self.result = result
        self.response = response


all_function_registry = {}
