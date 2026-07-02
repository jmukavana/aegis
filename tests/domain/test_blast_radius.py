from src.domain.topology.blast_radius import blast_radius
from tests.fakes import FakeServiceGraph

# checkout -> payments, inventory ; payments -> ledger
GRAPH = FakeServiceGraph(
    {
        "checkout": frozenset({"payments", "inventory"}),
        "payments": frozenset({"ledger"}),
    }
)


def test_transitive_dependents_are_the_blast_radius():
    # acting on ledger disrupts payments (calls ledger) and checkout (calls payments).
    assert blast_radius(GRAPH, "ledger") == frozenset({"payments", "checkout"})


def test_leaf_caller_has_no_dependents():
    # nobody calls checkout -> zero blast radius.
    assert blast_radius(GRAPH, "checkout") == frozenset()


def test_unknown_service_has_empty_blast_radius():
    assert blast_radius(GRAPH, "nginx") == frozenset()
