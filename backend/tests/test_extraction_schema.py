from app.schemas.extraction import ExtractedItem, ItemCategory


def test_extracted_item_minimal() -> None:
    item = ExtractedItem(
        title="Enkutatash party at Kuriftu",
        category=ItemCategory.EVENTS,
        confidence=0.87,
    )
    assert item.category == ItemCategory.EVENTS
    assert item.languages_detected == []
    assert item.location is None
