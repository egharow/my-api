-- A bank may refund several small fees in one credit. Each fee points at the credit that paid it back.
CREATE TABLE refund_links (
    fee_id    INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    credit_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    PRIMARY KEY (fee_id, credit_id)
);
