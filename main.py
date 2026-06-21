"""CiteWise — end-to-end demo entry point.

The full LangGraph pipeline is assembled in ``graph/graph.py`` and invoked here.
This is the scaffold: the graph is not wired yet. Each feature branch fills in
its agents and nodes, and this file will run the compiled graph on a sample
research question with a human-approval interrupt.
"""
from config import CITEWISE_MODEL


def main() -> None:
    print("CiteWise scaffold ready.")
    print(f"Configured model: {CITEWISE_MODEL}")
    print("The agent graph is under construction — see graph/graph.py.")


if __name__ == "__main__":
    main()
