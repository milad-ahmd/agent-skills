namespace Shop.Billing;

[Table("invoices")]
public sealed class Invoice
{
    public ClientAccount Client { get; set; }
}
