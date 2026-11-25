from enum import Enum


class Customer:
    """Someone who has placed an order."""
    class Meta:
        db_table = "customers"


class OrderStatus(Enum):
    PENDING = "pending"
    PAID = "paid"
    CANCELLED = "cancelled"

    def label(self):
        value = self.value
        return value.title()


class Order:
    def __init__(self, customer: Customer):
        self.customer = customer
        self.status = OrderStatus.PENDING


class OrderService:
    def place(self, order: Order) -> Order:
        return order


class HttpClient:
    pass


class Address:
    pass
