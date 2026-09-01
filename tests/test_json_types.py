import pytest

from fleasion.utils.json_types import (
    as_json_array,
    as_json_object,
    as_object_dict,
    as_object_list,
    is_json_value,
    is_object_dict,
    require_json_value,
    require_object_dict,
    require_object_list,
)


def test_as_json_object_accepts_nested_json_values() -> None:
    value: object = {
        'name': 'Fleasion',
        'enabled': True,
        'items': [1, None, {'nested': 2.5}],
    }

    assert as_json_object(value) == value


def test_as_json_object_rejects_non_json_values() -> None:
    assert as_json_object({'value': object()}) is None
    assert as_json_object({'value': [object()]}) is None
    assert not is_json_value({1: 'non-string key'})


def test_as_object_dict_only_validates_the_mapping_shape() -> None:
    marker = object()

    assert as_object_dict({'value': marker}) == {'value': marker}
    assert as_object_dict({1: marker}) is None
    assert as_object_dict([]) is None


def test_array_helpers_distinguish_shape_from_json_validation() -> None:
    marker = object()

    assert as_object_list([marker]) == [marker]
    assert as_json_array([marker]) is None
    assert as_json_array([1, {'nested': True}]) == [1, {'nested': True}]


def test_required_boundary_helpers_reject_invalid_shapes() -> None:
    assert require_json_value({'items': [1, None]}) == {'items': [1, None]}
    assert require_object_dict({'value': 1}) == {'value': 1}
    assert require_object_list([1]) == [1]
    assert is_object_dict({'value': object()})

    with pytest.raises(TypeError):
        require_json_value(object())
    with pytest.raises(TypeError):
        require_object_dict([])
    with pytest.raises(TypeError):
        require_object_list({})
