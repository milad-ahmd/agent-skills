package fulfilment

// Order is what the warehouse picks and ships.
type Order struct {
	ID    string
	Lines []string
}

type ShipmentStatus string

const (
	ShipmentPending   ShipmentStatus = "pending"
	ShipmentDelivered                // shipped and signed for
)

const (
	MaxRetries = 3
)
