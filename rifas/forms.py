from __future__ import annotations

import io

from django import forms
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile

from .models import BankAccount, Raffle, TicketPurchase


def validate_image_file(file):
    # Basic server-side validation (do not rely on client-side checks)
    content_type = getattr(file, "content_type", "") or ""
    if not content_type.startswith("image/"):
        raise ValidationError("El archivo debe ser una imagen (JPG/PNG/WebP).")

    # Allow bigger uploads; we optimize internally.
    hard_limit = 25 * 1024 * 1024  # 25MB
    if getattr(file, "size", 0) > hard_limit:
        raise ValidationError("La imagen es demasiado grande (máximo 25MB).")


def _optimize_image_upload(file, *, target_max_bytes: int = 6 * 1024 * 1024) -> InMemoryUploadedFile:
    """
    Reduce and recompress an uploaded image so it fits under target_max_bytes.
    Output is JPEG to maximize compatibility and size reduction.
    """
    from PIL import Image, ImageOps  # Pillow

    # Read image
    file.seek(0)
    img = Image.open(file)
    img = ImageOps.exif_transpose(img)  # correct orientation

    # Convert to RGB (drop alpha)
    if img.mode not in ("RGB", "L"):
        if "A" in img.getbands():
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            img = bg
        else:
            img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")

    # Try a couple of downscale + quality steps
    max_dim_candidates = [2200, 1800, 1600, 1400, 1200, 1000, 900]
    quality_candidates = [82, 75, 70, 65, 60, 55, 50, 45]

    best_bytes = None
    best_buf = None

    for max_dim in max_dim_candidates:
        w, h = img.size
        scale = min(1.0, max_dim / float(max(w, h)))
        if scale < 1.0:
            resized = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        else:
            resized = img

        for q in quality_candidates:
            buf = io.BytesIO()
            resized.save(buf, format="JPEG", quality=q, optimize=True, progressive=True)
            size = buf.tell()
            if best_bytes is None or size < best_bytes:
                best_bytes = size
                best_buf = buf
            if size <= target_max_bytes:
                break
        if best_bytes is not None and best_bytes <= target_max_bytes:
            break

    if best_buf is None or best_bytes is None:
        raise ValidationError("No se pudo procesar la imagen. Intenta con otra.")

    # If still too large, fail (rare)
    if best_bytes > target_max_bytes:
        raise ValidationError("La imagen es muy grande incluso después de optimizarla.")

    best_buf.seek(0)
    base_name = getattr(file, "name", "comprobante.jpg").rsplit(".", 1)[0]
    out_name = f"{base_name}.jpg"
    field_name = getattr(file, "field_name", "proof_image")
    return InMemoryUploadedFile(
        file=best_buf,
        field_name=field_name,
        name=out_name,
        content_type="image/jpeg",
        size=best_bytes,
        charset=None,
    )


PHONE_PREFIX_CHOICES = [
    ("809", "809"),
    ("829", "829"),
    ("849", "849"),
]


class TicketPurchaseForm(forms.ModelForm):
    phone_prefix = forms.ChoiceField(choices=PHONE_PREFIX_CHOICES, label="Prefijo")
    phone_number = forms.CharField(max_length=15, label="Número")
    bank_account = forms.ModelChoiceField(
        queryset=BankAccount.objects.none(),
        required=False,
        widget=forms.HiddenInput(),
        label="Banco",
    )
    accept_terms = forms.BooleanField(
        required=True,
        label="Acepto los términos y condiciones",
    )

    def __init__(self, *args, raffle: Raffle | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._raffle = raffle
        active_banks = BankAccount.objects.filter(is_active=True).order_by("sort_order", "created_at")[:4]
        self.fields["bank_account"].queryset = active_banks
        # If there are active bank accounts, require selection.
        self.fields["bank_account"].required = active_banks.exists()
        base_input = (
            "w-full rounded-xl border border-white/10 bg-slate-950/40 px-4 py-3 "
            "text-slate-100 placeholder:text-slate-500 outline-none "
            "focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-400/15"
        )
        base_select = (
            "w-full rounded-xl border border-white/10 bg-slate-950/40 px-3 py-3 "
            "text-slate-100 outline-none focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-400/15"
        )

        self.fields["full_name"].widget.attrs.setdefault("class", base_input)
        self.fields["full_name"].widget.attrs.setdefault("class", self.fields["full_name"].widget.attrs["class"] + " uppercase")
        self.fields["full_name"].widget.attrs.setdefault(
            "oninput",
            "this.value=this.value.replace(/[0-9]/g,'').toUpperCase()",
        )
        self.fields["full_name"].widget.attrs.setdefault(
            "onpaste",
            "setTimeout(() => { this.value=this.value.replace(/[0-9]/g,'').toUpperCase(); }, 0)",
        )
        # Email is required for purchases (admin notifications / customer follow-ups)
        self.fields["email"].required = True
        self.fields["email"].widget.attrs.setdefault("class", base_input)
        self.fields["email"].widget.attrs.setdefault("placeholder", "tu-correo@ejemplo.com")
        # Quantity is rendered with +/- buttons, so use group-friendly styling.
        self.fields["quantity"].widget.attrs.setdefault(
            "class",
            "w-full rounded-none border-y border-white/10 bg-slate-950/40 px-4 py-3 "
            "text-center text-slate-100 placeholder:text-slate-500 outline-none "
            "focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-400/15",
        )
        self.fields["quantity"].widget.attrs.setdefault("inputmode", "numeric")
        self.fields["quantity"].widget.attrs.setdefault("pattern", "[0-9]*")

        self.fields["phone_prefix"].widget.attrs.setdefault("class", base_select)
        self.fields["phone_number"].widget.attrs.setdefault("class", base_input)
        self.fields["phone_number"].widget.attrs.setdefault("inputmode", "numeric")
        self.fields["phone_number"].widget.attrs.setdefault("pattern", "[0-9]*")
        self.fields["phone_number"].widget.attrs.setdefault("maxlength", "7")
        self.fields["phone_number"].widget.attrs.setdefault("minlength", "7")
        self.fields["phone_number"].widget.attrs.setdefault("oninput", "this.value=this.value.replace(/\\D/g,'')")
        self.fields["phone_number"].widget.attrs.setdefault("placeholder", "1234567")

        self.fields["proof_image"].widget.attrs.setdefault(
            "class",
            "block w-full text-sm text-slate-300 file:mr-4 file:rounded-xl "
            "file:border-0 file:bg-white/10 file:px-4 file:py-2 file:text-sm "
            "file:font-semibold file:text-white hover:file:bg-white/15",
        )

        # If editing an instance, split existing phone into prefix + number
        if self.instance and getattr(self.instance, "phone", None):
            digits = "".join(ch for ch in (self.instance.phone or "") if ch.isdigit())
            if len(digits) >= 3:
                self.fields["phone_prefix"].initial = digits[:3]
                self.fields["phone_number"].initial = digits[3:]

        # Enforce minimum purchase in UI (mobile-first)
        if raffle:
            min_q = int(getattr(raffle, "min_purchase_quantity", 1) or 1)
            self.fields["quantity"].widget.attrs["min"] = str(min_q)
            if not self.data and not self.initial.get("quantity"):
                self.fields["quantity"].initial = min_q

    class Meta:
        model = TicketPurchase
        fields = ["full_name", "email", "bank_account", "quantity", "proof_image"]
        widgets = {
            "full_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "quantity": forms.NumberInput(attrs={"min": "1", "step": "1"}),
        }

    proof_image = forms.ImageField(validators=[validate_image_file])

    def clean_proof_image(self):
        f = self.cleaned_data.get("proof_image")
        if not f:
            return f
        # Transparente: si supera 6MB, optimizamos internamente.
        if getattr(f, "size", 0) > 6 * 1024 * 1024:
            return _optimize_image_upload(f, target_max_bytes=6 * 1024 * 1024)
        return f

    def clean_full_name(self):
        name = (self.cleaned_data.get("full_name") or "").strip()
        if any(ch.isdigit() for ch in name):
            raise ValidationError("El nombre no debe contener números.")
        if len(name) < 3:
            raise ValidationError("Ingresa tu nombre completo.")
        return name.upper()

    def clean_phone_number(self):
        raw = (self.cleaned_data.get("phone_number") or "").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            raise ValidationError("Ingresa el número de teléfono.")
        # República Dominicana: 7 dígitos después del prefijo (normal).
        if len(digits) != 7:
            raise ValidationError("El número debe tener 7 dígitos (sin el prefijo).")
        return digits

    def clean_quantity(self):
        qty = int(self.cleaned_data["quantity"])
        raffle = getattr(self, "_raffle", None)
        if raffle and qty < int(getattr(raffle, "min_purchase_quantity", 1) or 1):
            raise ValidationError(f"El mínimo de compra para esta rifa es {raffle.min_purchase_quantity} boletos.")
        return qty

    def save(self, commit=True):
        instance: TicketPurchase = super().save(commit=False)
        prefix = self.cleaned_data.get("phone_prefix") or ""
        number = self.cleaned_data.get("phone_number") or ""
        instance.phone = f"{prefix}{number}"
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class TicketLookupForm(forms.Form):
    raffle = forms.ModelChoiceField(
        queryset=Raffle.objects.all().order_by("-created_at"),
        empty_label="Selecciona una rifa",
    )
    phone_prefix = forms.ChoiceField(choices=PHONE_PREFIX_CHOICES, label="Prefijo")
    phone_number = forms.CharField(max_length=15, label="Número")
    reference = forms.CharField(
        max_length=12,
        required=False,
        label="Código (opcional)",
        help_text="Recomendado: el código que te mostramos al comprar.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        base_input = (
            "w-full rounded-xl border border-white/10 bg-slate-950/40 px-4 py-3 "
            "text-slate-100 placeholder:text-slate-500 outline-none "
            "focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-400/15"
        )
        base_select = (
            "w-full rounded-xl border border-white/10 bg-slate-950/40 px-3 py-3 "
            "text-slate-100 outline-none focus:border-emerald-400/60 focus:ring-2 focus:ring-emerald-400/15"
        )
        self.fields["raffle"].widget.attrs.setdefault("class", base_select)
        self.fields["phone_prefix"].widget.attrs.setdefault("class", base_select)
        self.fields["phone_number"].widget.attrs.setdefault("class", base_input)
        self.fields["phone_number"].widget.attrs.setdefault("inputmode", "numeric")
        self.fields["phone_number"].widget.attrs.setdefault("pattern", "[0-9]*")
        self.fields["phone_number"].widget.attrs.setdefault("maxlength", "7")
        self.fields["phone_number"].widget.attrs.setdefault("minlength", "7")
        self.fields["phone_number"].widget.attrs.setdefault("oninput", "this.value=this.value.replace(/\\D/g,'')")
        self.fields["phone_number"].widget.attrs.setdefault("placeholder", "1234567")
        self.fields["reference"].widget.attrs.setdefault("class", base_input)

    def clean_phone_number(self):
        raw = (self.cleaned_data.get("phone_number") or "").strip()
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not digits:
            raise ValidationError("Ingresa el número de teléfono.")
        if len(digits) != 7:
            raise ValidationError("El número debe tener 7 dígitos (sin el prefijo).")
        return digits

    def clean_reference(self):
        ref = (self.cleaned_data.get("reference") or "").strip().upper()
        return ref

    def clean(self):
        cleaned = super().clean()
        prefix = cleaned.get("phone_prefix") or ""
        number = cleaned.get("phone_number") or ""
        # Keep backwards-compatible key used by the view.
        cleaned["phone"] = f"{prefix}{number}"
        return cleaned


class AdminPasswordRecoverForm(forms.Form):
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))

