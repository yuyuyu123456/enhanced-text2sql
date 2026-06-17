# Dual Address Design Documentation for Department Store Database

## Overview

This document outlines the dual address design within the `department_store` database, explaining the rationale for its implementation, usage scenarios, and the trade-offs associated with it.

## 1. Database Entities and Schema

### 1.1 Customers
  - **customer_address**: A simple, denormalized VARCHAR field that stores the customer's current address. This is a direct and quick way to access the customer's current address without needing to join multiple tables.

### 1.2 Addresses
- **addresses**: A normalized table that contains all the historical addresses. This table includes the address details and is independent of customers or suppliers.
  - Fields:
    - address_id (Primary Key)
    - street
    - city
    - state
    - zip_code

### 1.3 CustomerAddresses
- **customer_addresses**: A junction table that links customers to their historical addresses, along with the date ranges during which each address was valid.
  - Fields:
    - customer_id (Foreign Key)
    - address_id (Foreign Key)
    - date_from
    - date_to

### 1.4 Suppliers
- **supplier_addresses**: Similar to `customer_addresses`, this junction table links suppliers to their historical addresses and date ranges.

## 2. Rationale for Dual Address Design

Both the denormalized `customer_address` and the normalized `customer_addresses` (and `supplier_addresses`) tables serve distinct purposes and cater to different use cases:

### 2.1 Why Both Exist

- **Immediate Access**: The `customer_address` field in the `customers` table provides immediate and direct access to the customer's current address, which is beneficial for applications that require quick data retrieval.
- **Historical Record**: The `addresses` and `customer_addresses` tables store a comprehensive history of all addresses a customer has had over time, which is valuable for reporting, analytics, and tracking customer movement patterns.

### 2.2 When to Use Each

- **Use `customer_address` when:**
  - The current address of the customer is needed immediately, without the need for historical data.
  - Queries involving the current address are expected to be frequent.

- **Use `addresses` and `customer_addresses` when:**
  - Historical address data is required for analysis or reporting.
  - The customer's address history needs to be maintained and accessible across different time periods.

## 3. Trade-offs

### 3.1 Pros

- **Immediate Access**: Quick access to the current address improves application performance, especially for frequently accessed fields.
- **Normalization**: The `addresses` and `customer_addresses` tables enforce data integrity and normalization, reducing redundancy and improving data consistency.
- **Flexibility**: The historical data can be easily manipulated and analyzed without affecting the customer's current address.

### 3.2 Cons

- **Complexity**: The dual address design increases the complexity of queries, especially when joining multiple tables to retrieve historical addresses.
- **Storage**: The normalized tables require more storage space compared to a denormalized approach.
- **Performance**: Queries involving joins may experience performance degradation, especially as the database grows in size.

## 4. Sample Data

Given the scenario where customer id=1 has a direct address "75099 Tremblay Port, SC 80546" and three historical addresses:

```
Customer id=1:
customer_address: 75099 Tremblay Port, SC 80546

customer_addresses:
customer_id | address_id | date_from | date_to
------------|------------|------------|---------
1            | 100        | 2021-01-01 | 2021-12-31
1            | 101        | 2022-01-01 | 2022-06-30
1            | 102        | 2023-07-01 | 2023-12-31
```

This design allows for efficient retrieval of the current address while maintaining a comprehensive historical record of the customer's addresses.