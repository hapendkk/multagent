"""Сборка графа LangGraph: порядок шагов, цикл доработки и шлюзы."""
from langgraph.graph import END, START, StateGraph

import config
from observability import traced_node
from agents import art_director, ui_generator, ux_researcher, validator
from state import PixelState


def after_art_director(state: PixelState):
    """Шлюз 1: если бриф отклонён, дальше не идём."""
    return "stop" if state["outcome"] == "rejected" else "go"


def after_validator(state: PixelState):
    """Шлюз 2 и цикл доработки."""
    if state["outcome"] == "fail" and state["attempt"] < config.MAX_ATTEMPTS:
        return "retry"
    return "done"


def build_graph(checkpointer=None):
    g = StateGraph(PixelState)
    g.add_node("art_director", traced_node("art_director", art_director))
    g.add_node("ux_researcher", traced_node("ux_researcher", ux_researcher))
    g.add_node("ui_generator", traced_node("ui_generator", ui_generator))
    g.add_node("validator", traced_node("validator", validator))

    g.add_edge(START, "art_director")
    g.add_conditional_edges("art_director", after_art_director,
                            {"go": "ux_researcher", "stop": END})
    g.add_edge("ux_researcher", "ui_generator")
    g.add_edge("ui_generator", "validator")
    g.add_conditional_edges("validator", after_validator,
                            {"retry": "ui_generator", "done": END})
    return g.compile(checkpointer=checkpointer)
