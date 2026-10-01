import math

import migjorn
import numpy as np
import pytest

from radmodeling.mcnp.geometry import (
    Evaluator,
    Geometry,
    McnpParseError,
    Transform,
    expand_numbers,
    macrobody_facets,
    parse_geometry,
    surface_function,
)

RNG = np.random.default_rng(3)


def sense(kind, values, points):
    points = np.atleast_2d(np.asarray(points, dtype=float))
    return surface_function(kind, values)(points[:, 0], points[:, 1], points[:, 2]) > 0


@pytest.mark.parametrize(
    ("kind", "values", "inside", "outside"),
    [
        ("p", [1, 1, 0, 2], [0, 0, 0], [3, 3, 0]),
        ("px", [2], [1, 9, 9], [3, -9, -9]),
        ("so", [2], [1, 1, 1], [2, 2, 2]),
        ("s", [1, 2, 3, 1], [1, 2, 3.9], [1, 2, 4.1]),
        ("sy", [5, 1], [0, 5.5, 0], [0, 6.5, 0]),
        ("c/z", [1, 1, 1], [1.5, 1.5, 100], [0, 0, 0]),
        ("cx", [1], [50, 0.5, 0.5], [0, 1, 1]),
        ("k/z", [0, 0, 1, 1], [0, 0.5, 2], [2, 0, 1]),
        ("kz", [1, 1, 1], [0, 0.5, 2], [0, 0.5, 0]),  # other nappe is outside
        ("kz", [1, 1, -1], [0, 0.5, 0], [0, 0.5, 2]),
        ("sq", [1, 1, 1, 0, 0, 0, -4, 1, 1, 1], [2, 2, 2], [3.1, 1, 1]),
        ("gq", [1, 1, 0, 0, 0, 0, 0, 0, 0, -1], [0.5, 0, 9], [1, 1, 0]),
        ("tz", [0, 0, 0, 5, 1, 1], [5, 0, 0.5], [0, 0, 0]),
        ("tx", [0, 0, 0, 5, 1, 2], [0.5, 0, 6.5], [0, 0, 5 + 2.5]),
        ("x", [0, 1, 2, 1], [1, 0.5, 0], [1, 2, 0]),  # cylinder
        ("z", [0, 1, 2, 2], [0, 1.2, 1], [0, 1.8, 1]),  # cone
    ],
)
def test_surface_sense(kind, values, inside, outside):
    assert not sense(kind, values, inside)[0]
    assert sense(kind, values, outside)[0]


def test_plane_by_three_points_has_origin_on_negative_side():
    assert not sense("p", [1, 0, 0, 0, 1, 0, 0, 0, 1], [0, 0, 0])[0]
    assert not sense("p", [0, 0, 1, 1, 0, 1, 0, 1, 1], [0, 0, 0])[0]
    assert sense("p", [0, 0, 1, 1, 0, 1, 0, 1, 1], [0, 0, 2])[0]


def test_rpp_facets_order():
    facets = macrobody_facets("rpp", [0, 1, 0, 2, 0, 3])
    point = np.array([[0.5, 1, 1.5]])
    values = [f(point[:, 0], point[:, 1], point[:, 2])[0] for f in facets]
    assert values == pytest.approx([-0.5, -0.5, -1, -1, -1.5, -1.5])


def test_expand_numbers_shortcuts():
    assert expand_numbers(["1", "2r", "3"]) == [1, 1, 1, 3]
    assert expand_numbers(["0", "3i", "4"]) == [0, 1, 2, 3, 4]
    assert expand_numbers(["2", "3m"]) == [2, 6]


def test_geometry_parser_precedence():
    node = parse_geometry("-1 2 : 3 #(4 : -5) #7")
    assert node.op == "or"
    assert node.children[0].op == "and"
    assert node.children[1].op == "and"
    assert node.surfaces() == {"1", "2", "3", "4", "5"}
    assert node.complemented_cells() == {7}
    with pytest.raises(McnpParseError):
        parse_geometry("(1 2")


def test_transform_degrees_and_inverse():
    # Auxiliary x' axis along main y: rotation of 90 degrees around z
    transform = Transform.from_values(
        [1, 0, 0, 90, 0, 90, 180, 90, 90, 90, 90, 0], degrees=True
    )
    assert np.allclose(transform.to_auxiliary(np.array([[1.0, 2, 0]])), [[2, 0, 0]])
    inverse = Transform.from_values([1, 0, 0, *np.eye(3).ravel(), -1], degrees=False)
    assert np.allclose(inverse.to_auxiliary(np.array([[0.0, 0, 0]])), [[1, 0, 0]])


MODEL = """Geometry test
1 1 -7.9  -1 -2 (3 : -4)   imp:n=1
2 0       -5 #1 #3 #4      imp:n=1
3 like 1 but mat=2 rho=-1  imp:n=1
4 0       -6.1 -6.2 -6.3   imp:n=1
9 0       5                imp:n=0

1 px 10
2 so 20
3 1 pz 0
4 pz -50
5 so 100
6 rcc 0 50 0 0 0 10 3
7 gq 1 2r 0 0 0 0 0 0 -4

tr1 0 0 -1
"""


def test_model_classification():
    geometry = Geometry(migjorn.parse(MODEL))
    assert geometry.cells[3].material == 2
    assert geometry.cells[3].density == -1
    points = np.array(
        [
            [0, 0, -0.5],  # cell 1: above the translated plane z = -1
            [15, 0, 0],  # cell 2
            [0, 50, 5],  # cell 4: inside the RCC
            [0, 50, 12],  # cell 2: above the RCC top facet
            [0, 0, 200],  # cell 9
        ]
    )
    evaluator = geometry.evaluator(points)
    owner, count = evaluator.cell_index([1, 2, 4, 9])
    assert owner.tolist() == [1, 2, 4, 2, 9]
    assert count.tolist() == [1] * 5  # no overlaps among the level-0 cells
    # Cell 3 is a copy of cell 1 and overlaps it
    assert evaluator.cell(3).tolist() == evaluator.cell(1).tolist()
    # The nR shortcut, which migjorn drops, is expanded
    gq = geometry.surfaces["7"].evaluate(np.array([[1.0, 1, 1]]), {})
    assert gq[0] == pytest.approx(-1)


def test_monte_carlo_volume_of_cells():
    geometry = Geometry(migjorn.parse(MODEL))
    n = 400_000
    points = RNG.uniform(-5, 5, (n, 3)) + [0, 50, 5]
    inside = geometry.evaluator(points).cell(4)
    assert inside.mean() * 1000 == pytest.approx(math.pi * 9 * 10, rel=0.02)


def test_unsupported_features_fail_only_when_used():
    text = MODEL.replace("4 pz -50", "4 pz -50\n8 ell 0 0 0 1 1 1 3").replace(
        "3 like 1 but", "3 like 1 but trcl=(1 0 0)"
    )
    geometry = Geometry(migjorn.parse(text))
    evaluator = geometry.evaluator(np.zeros((1, 3)))
    evaluator.cell(1)
    with pytest.raises(McnpParseError, match="TRCL"):
        evaluator.cell(3)


BOUNDS_MODEL = """Bounding boxes must never change the result
1 0  -1 2 -3 (4 : -5)           imp:n=1
2 0  -6 #1                      imp:n=1
3 0  -7 -8.1 -8.2 -8.3          imp:n=1
4 0  -9 : -10                   imp:n=1
5 0  -11 12 #(-13 14)           imp:n=1
6 0  -14 -1 13                  imp:n=1
7 0  -15 #1 #2 #3 #4 #5 #6      imp:n=1
8 0  15                         imp:n=0

1 px 10
2 px -10
3 2 so 20
4 pz 0
5 1 c/z 3 3 2
6 rpp -30 30 -30 30 -30 30
7 3 box -5 -5 -5 10 0 0 0 10 0 0 0 10
8 rcc 0 0 -40 0 0 30 5
9 s 25 0 0 3
10 2 tz 0 -20 0 6 1 2
11 1 rcc 0 20 0 0 0 10 4
12 cz 1
13 pz 5
14 cy 30
15 so 100

tr1 1 2 3 0.8 0.6 0 -0.6 0.8 0 0 0 1
*tr2 0 0 5 30 60 90 120 30 90 90 90 0
*tr3 5 5 5 45 45 90 135 45 90 90 90 0 -1
"""


def test_bounding_boxes_do_not_change_classification():
    geometry = Geometry(migjorn.parse(BOUNDS_MODEL))
    points = RNG.uniform(-60, 60, (200_000, 3))
    fast = geometry.evaluator(points)
    slow = Evaluator(geometry, points, subsets=False)
    assert geometry.cell_box(3) is not None
    assert geometry.cell_box(8) is None  # the outside world
    for number in geometry.cells:
        assert np.array_equal(fast.cell(number), slow.cell(number)), number
