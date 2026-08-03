"""
Common Cypher queries for knowledge graph operations.

This module provides reusable query templates for common graph operations.
"""

# Entity queries
GET_ENTITY_BY_ID = """
MATCH (e:Entity {id: $id, tenant_id: $tenant_id})
RETURN e
"""

GET_ENTITIES_BY_TYPE = """
MATCH (e:Entity)
WHERE e.tenant_id = $tenant_id AND e.type = $type
RETURN e
ORDER BY e.name
SKIP $skip
LIMIT $limit
"""

SEARCH_ENTITIES_FULLTEXT = """
CALL db.index.fulltext.queryNodes('entity_name_search', $query)
YIELD node, score
WHERE node.tenant_id = $tenant_id
RETURN node, score
ORDER BY score DESC
LIMIT $limit
"""

# Relationship queries
GET_ENTITY_RELATIONSHIPS = """
MATCH (e:Entity {id: $id, tenant_id: $tenant_id})-[r]-(related)
WHERE related.tenant_id = $tenant_id
RETURN r, related
"""

GET_RELATIONSHIP_PATH = """
MATCH path = shortestPath(
    (start:Entity {id: $start_id, tenant_id: $tenant_id})-[*..5]-
    (end:Entity {id: $end_id, tenant_id: $tenant_id})
)
RETURN path
"""

# Graph traversal queries
GET_ENTITY_NEIGHBORHOOD = """
MATCH (e:Entity {id: $id, tenant_id: $tenant_id})
CALL apoc.path.subgraphAll(e, {
    maxLevel: $depth,
    limit: $limit
}) YIELD nodes, relationships
RETURN nodes, relationships
"""

GET_CONNECTED_COMPONENTS = """
MATCH (e:Entity {tenant_id: $tenant_id})
CALL apoc.path.subgraphNodes(e, {maxLevel: 10}) YIELD node
WITH collect(DISTINCT node) as component
RETURN component
ORDER BY size(component) DESC
"""

# Analytics queries
COUNT_ENTITIES_BY_TYPE = """
MATCH (e:Entity {tenant_id: $tenant_id})
RETURN e.type as type, count(e) as count
ORDER BY count DESC
"""

COUNT_RELATIONSHIPS_BY_TYPE = """
MATCH (e:Entity {tenant_id: $tenant_id})-[r]-()
RETURN type(r) as relationship_type, count(r) as count
ORDER BY count DESC
"""

GET_MOST_CONNECTED_ENTITIES = """
MATCH (e:Entity {tenant_id: $tenant_id})-[r]-()
WITH e, count(r) as degree
ORDER BY degree DESC
LIMIT $limit
RETURN e, degree
"""

GET_ENTITY_STATS = """
MATCH (e:Entity {tenant_id: $tenant_id})
WITH count(e) as total_entities
MATCH (:Entity {tenant_id: $tenant_id})-[r]-()
WITH total_entities, count(r) as total_relationships
RETURN total_entities, total_relationships
"""

# Cleanup queries
DELETE_TENANT_DATA = """
MATCH (e:Entity {tenant_id: $tenant_id})
DETACH DELETE e
RETURN count(e) as deleted
"""

DELETE_JOB_ENTITIES = """
MATCH (e:Entity)
WHERE e.source_job_id = $job_id AND e.tenant_id = $tenant_id
DETACH DELETE e
RETURN count(e) as deleted
"""

# Merge queries for deduplication
FIND_DUPLICATE_ENTITIES = """
MATCH (e1:Entity {tenant_id: $tenant_id})
MATCH (e2:Entity {tenant_id: $tenant_id})
WHERE e1.normalized_name = e2.normalized_name
AND e1.type = e2.type
AND id(e1) < id(e2)
RETURN e1, e2, e1.name as name
ORDER BY name
"""

MERGE_DUPLICATE_ENTITIES = """
MATCH (keep:Entity {id: $keep_id, tenant_id: $tenant_id})
MATCH (remove:Entity {id: $remove_id, tenant_id: $tenant_id})
// Move relationships from remove to keep
MATCH (remove)-[r]->(other)
MERGE (keep)-[newRel:RELATED_TO]->(other)
SET newRel = properties(r)
DELETE r
WITH keep, remove
// Move incoming relationships
MATCH (other)-[r]->(remove)
MERGE (other)-[newRel:RELATED_TO]->(keep)
SET newRel = properties(r)
DELETE r
// Delete the duplicate
DETACH DELETE remove
RETURN keep
"""
