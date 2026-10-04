"""Schema.org and Open Graph mapping definitions and vocabularies.

Provides vocabulary mappings for Schema.org types and Open Graph metadata
to collapse structured metadata into Redstring domain entity classifications.
"""

from __future__ import annotations

# Mapping from Schema.org types to our entity types
SCHEMA_TYPE_MAP: dict[str, str] = {
    # Person types
    "Person": "person",
    "Author": "person",
    # Organization types
    "Organization": "organization",
    "Corporation": "organization",
    "LocalBusiness": "organization",
    "Company": "organization",
    "EducationalOrganization": "organization",
    "GovernmentOrganization": "organization",
    "NGO": "organization",
    "SportsOrganization": "organization",
    # Location types
    "Place": "location",
    "City": "location",
    "Country": "location",
    "AdministrativeArea": "location",
    "GeoCoordinates": "location",
    "PostalAddress": "location",
    "Landmark": "location",
    # Event types
    "Event": "event",
    "BusinessEvent": "event",
    "ChildrensEvent": "event",
    "ComedyEvent": "event",
    "CourseInstance": "event",
    "DanceEvent": "event",
    "DeliveryEvent": "event",
    "EducationEvent": "event",
    "ExhibitionEvent": "event",
    "Festival": "event",
    "FoodEvent": "event",
    "Hackathon": "event",
    "LiteraryEvent": "event",
    "MusicEvent": "event",
    "PublicationEvent": "event",
    "SaleEvent": "event",
    "ScreeningEvent": "event",
    "SocialEvent": "event",
    "SportsEvent": "event",
    "TheaterEvent": "event",
    "VisualArtsEvent": "event",
    # Product types
    "Product": "product",
    "ProductModel": "product",
    "IndividualProduct": "product",
    "SoftwareApplication": "product",
    "MobileApplication": "product",
    "WebApplication": "product",
    "Book": "product",
    "Movie": "product",
    "MusicAlbum": "product",
    "VideoGame": "product",
    # Document types
    "Article": "document",
    "NewsArticle": "document",
    "BlogPosting": "document",
    "ScholarlyArticle": "document",
    "TechArticle": "document",
    "Report": "document",
    "WebPage": "document",
    "CreativeWork": "document",
    # Date-related
    "Date": "date",
    "DateTime": "date",
    # Concept types
    "Thing": "concept",
    "Intangible": "concept",
}

# Common property fields to extract from Schema.org items
SCHEMA_PROPERTY_FIELDS: tuple[str, ...] = (
    "url",
    "image",
    "logo",
    "email",
    "telephone",
    "address",
    "location",
    "geo",
    "startDate",
    "endDate",
    "datePublished",
    "dateCreated",
    "dateModified",
    "author",
    "creator",
    "publisher",
    "brand",
    "jobTitle",
    "worksFor",
    "memberOf",
    "price",
    "priceCurrency",
    "offers",
    "aggregateRating",
    "review",
    "ratingValue",
    "category",
    "genre",
    "keywords",
)

# Fields that may contain nested entities
SCHEMA_NESTED_FIELDS: tuple[str, ...] = (
    "author",
    "creator",
    "publisher",
    "brand",
    "worksFor",
    "memberOf",
    "performer",
    "organizer",
    "location",
    "address",
    "sponsor",
    "funder",
    "mentions",
    "about",
)


def map_open_graph_type(og_type: str) -> str:
    """Map Open Graph type to entity type."""
    og_type = og_type.lower()

    if og_type in ("website", "article", "blog"):
        return "document"
    if og_type == "profile":
        return "person"
    if og_type in ("product", "book", "music.album", "video.movie"):
        return "product"
    if og_type in ("place", "business.business"):
        return "location"
    if og_type in ("music.song", "music.playlist", "video.episode"):
        return "document"
    return "concept"
