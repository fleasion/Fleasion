from fleasion.utils.json_types import as_json_object, as_object_dict, is_json_value


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
