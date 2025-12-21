"""Tests for PII masking utilities."""

from libs.alerts.pii import mask_email, mask_phone, mask_recipient, mask_webhook


class TestMaskEmail:
    """Test email masking."""

    def test_standard_email(self):
        """Test masking standard email address."""
        result = mask_email("john.doe@example.com")
        assert result == "***.com"  # Shows last 4 chars only

    def test_short_email(self):
        """Test masking short email."""
        result = mask_email("a@b.co")
        assert result == "***b.co"  # Last 4 chars

    def test_very_short_email(self):
        """Test masking very short email."""
        result = mask_email("a@b")
        assert result == "***"

    def test_four_char_email(self):
        """Test masking exactly 4 char email."""
        result = mask_email("abcd")
        assert result == "***abcd"


class TestMaskPhone:
    """Test phone number masking."""

    def test_us_phone(self):
        """Test masking US phone number."""
        result = mask_phone("+15551234567")
        assert result == "***4567"  # Last 4 chars

    def test_short_phone(self):
        """Test short phone number."""
        result = mask_phone("123")
        assert result == "***"

    def test_four_digit_phone(self):
        """Test 4 digit phone."""
        result = mask_phone("1234")
        assert result == "***1234"


class TestMaskWebhook:
    """Test webhook URL masking."""

    def test_slack_webhook(self):
        """Test masking Slack webhook URL."""
        url = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXX"
        result = mask_webhook(url)
        assert result == "***XXXX"  # Last 4 chars

    def test_short_webhook(self):
        """Test masking short URL."""
        result = mask_webhook("abc")
        assert result == "***"


class TestMaskRecipient:
    """Test channel-aware recipient masking."""

    def test_email_channel(self):
        """Test masking email channel recipient."""
        result = mask_recipient("user@example.com", "email")
        assert result == "***.com"  # Uses mask_email

    def test_sms_channel(self):
        """Test masking SMS channel recipient."""
        result = mask_recipient("+15551234567", "sms")
        assert result == "***4567"  # Uses mask_phone

    def test_slack_channel(self):
        """Test masking Slack channel recipient."""
        result = mask_recipient("https://hooks.slack.com/x", "slack")
        assert result == "***om/x"  # Uses mask_webhook, last 4 chars

    def test_unknown_channel(self):
        """Test unknown channel uses email masking."""
        result = mask_recipient("some-recipient-value", "unknown")
        assert result == "***alue"  # Default to email masking
