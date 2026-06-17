# Temporal Rules for Date-Ranged Tables

## Critical: Words like "currently", "now", "at present", "active" map to `date_xxx_to IS NULL`

When a question uses any of these temporal keywords, you MUST add a WHERE clause
filtering for currently-active records. The pattern is always:

```sql
WHERE <table>.date_xxx_to IS NULL
```

### Affected tables and their "current" columns

| Table | Date-From Column | Date-To Column | "Currently" Filter |
|--------|-----------------|----------------|---------------------|
| `customer_addresses` | `date_from` | `date_to` | `WHERE date_to IS NULL` |
| `supplier_addresses` | `date_from` | `date_to` | `WHERE date_to IS NULL` |
| `product_suppliers` | `date_supplied_from` | `date_supplied_to` | `WHERE date_supplied_to IS NULL` |
| `staff_department_assignments` | `date_assigned_from` | `date_assigned_to` | `WHERE date_assigned_to IS NULL` |

### Keyword → Filter Mapping

| Temporal Keyword | SQL Filter |
|---|---|
| "currently", "now", "at present" | `date_xxx_to IS NULL` |
| "currently assigned" | `staff_department_assignments.date_assigned_to IS NULL` |
| "currently supplies", "currently supply" | `product_suppliers.date_supplied_to IS NULL` |
| "current address" | `customer_addresses.date_to IS NULL` |
| "active", "still active" | `date_xxx_to IS NULL` |
| "in the past", "previously", "formerly" | `date_xxx_to IS NOT NULL` |
| "during 2020", "in 2023", specific time range | `date_from <= '...' AND (date_to >= '...' OR date_to IS NULL)` |

### Examples

**Q: "How many distinct products does each supplier currently supply?"**
```sql
SELECT s.supplier_name, COUNT(DISTINCT ps.product_id)
FROM Suppliers s
JOIN Product_Suppliers ps ON s.supplier_id = ps.supplier_id
WHERE ps.date_supplied_to IS NULL  -- ← "currently" requires this!
GROUP BY s.supplier_id;
```

**Q: "Which departments have no staff currently assigned?"**
```sql
SELECT d.department_name
FROM Departments d
LEFT JOIN Staff_Department_Assignments sda
  ON d.department_id = sda.department_id
  AND sda.date_assigned_to IS NULL  -- ← "currently"!
WHERE sda.staff_id IS NULL;
```

**Q: "List all staff names along with their current department."**
```sql
SELECT s.staff_name, d.department_name
FROM Staff s
JOIN Staff_Department_Assignments sda
  ON s.staff_id = sda.staff_id AND sda.date_assigned_to IS NULL
JOIN Departments d ON sda.department_id = d.department_id;
```

### Without "currently" — no filter needed

**Q: "How many distinct products does each supplier supply?"** (no temporal word)
```sql
SELECT s.supplier_name, COUNT(DISTINCT ps.product_id)
FROM Suppliers s
JOIN Product_Suppliers ps ON s.supplier_id = ps.supplier_id
GROUP BY s.supplier_id;
-- No date filter — includes all historical records


---

# COUNT(DISTINCT) Rule for Junction/Temporal Tables

## Critical: Use COUNT(DISTINCT) when counting entities through junction tables

Junction tables and temporal tables can have multiple rows for the same entity
(different time periods, different products, etc.). Without DISTINCT, you double-count.

### Tables requiring COUNT(DISTINCT)

| Table | Counting Column | Reason |
|--------|----------------|--------|
| `staff_department_assignments` | `staff_id` | Staff can have multiple assignments across time |
| `customer_addresses` | `customer_id` | Customer can have multiple historical addresses |
| `supplier_addresses` | `supplier_id` | Supplier can have multiple historical addresses |
| `product_suppliers` | `product_id` or `supplier_id` | Multiple supply periods for same product/supplier |
| `order_items` | `order_id` | Multiple items per order |

### Examples

**Q: "List all department names and the number of staff currently working in each department."**
```sql
SELECT d.department_name, COUNT(DISTINCT sda.staff_id) AS staff_count
FROM Departments d
JOIN Staff_Department_Assignments sda ON d.department_id = sda.department_id
WHERE sda.date_assigned_to IS NULL
GROUP BY d.department_id;
-- COUNT(DISTINCT staff_id): a staff member might appear in multiple assignment rows
```

**Q: "How many products does each supplier provide?"**
```sql
SELECT s.supplier_name, COUNT(DISTINCT ps.product_id)
FROM Suppliers s
JOIN Product_Suppliers ps ON s.supplier_id = ps.supplier_id
GROUP BY s.supplier_id;
-- DISTINCT: a product might have multiple supply periods
```

### Rule of thumb
When counting **people** (staff, customers) or **distinct entities** through a
junction/temporal table, always use `COUNT(DISTINCT <id>)`.


---

# Column Selection Rules: Human-Readable vs Technical IDs

## Critical: Questions asking "which", "who", "name of" → return display names, NOT technical IDs

When a user asks "which staff", "which customers", "who", "names of", they expect
**human-readable identifiers** (names, titles) — not database primary keys.

### ID → Human-Readable Column Mapping

| Table | Technical ID (don't return alone) | Human-Readable (return this) |
|--------|----------------------------------|------------------------------|
| `staff` | `staff_id` | `staff_name`, `staff_gender` |
| `customers` | `customer_id` | `customer_name` |
| `suppliers` | `supplier_id` | `supplier_name` |
| `products` | `product_id` | `product_name`, `product_type_code` |
| `departments` | `department_id` | `department_name` |
| `department_stores` | `dept_store_id` | `store_name` |
| `department_store_chain` | `dept_store_chain_id` | `dept_store_chain_name` |

### Keyword → Column Selection

| Question Keyword | Return These Columns |
|---|---|
| "which staff", "who", "staff names" | `staff_name` (NOT `staff_id`) |
| "which customers", "customer names" | `customer_name` (NOT `customer_id`) |
| "which products", "product names" | `product_name` (NOT `product_id`) |
| "name and gender" | `staff_name`, `staff_gender` |
| "name and phone" | `supplier_name`, `supplier_phone` / `customer_name`, `customer_phone` |
| "id and name" (explicitly asks for both) | both `_id` and `_name` columns |

### Examples

**Q: "Which staff have held the title of Manager?"**
```sql
SELECT staff_name FROM staff ...  -- ✓ human-readable
-- NOT: SELECT staff_id FROM staff ...  -- ✗ technical ID, meaningless to user
```

**Q: "What are the id and name of customers?"** (explicitly asks for id)
```sql
SELECT customer_id, customer_name FROM customers ...  -- ✓ both requested
```

**Q: "List the names of suppliers"**
```sql
SELECT supplier_name FROM suppliers ...  -- ✓ name requested
```

### Rule: When in doubt, include the name column. Only return bare IDs when the question explicitly asks for "id" or "ids".
```
