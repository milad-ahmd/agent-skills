package shop.fulfilment

data class Shipment(val order: Order, val parcels: List<String>)

enum class ParcelSize(val grams: Int) {
    SMALL(500),
    LARGE(5000);
}
