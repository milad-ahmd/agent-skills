package shop.billing;

public class ClientAccount {
    private String clientId;
}

public enum InvoiceStatus {
    DRAFT("d"),
    ISSUED("i"),
    VOIDED("v");

    private final String code;
    InvoiceStatus(String code) { this.code = code; }
}
