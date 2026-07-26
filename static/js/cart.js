/**
 * Sajilo Pasal — Shopping Cart
 *
 * Pure localStorage cart. No server calls, no login required.
 * Survives temporary network interruptions (offline cart persistence
 * per the SDD requirement).
 *
 * Storage format in localStorage under key "sajilo_cart_<shop_id>":
 * {
 *   "<product_id>": {
 *     "id":       <int>,
 *     "name":     <string>,
 *     "price":    <float>,   // stored as number for arithmetic
 *     "quantity": <int>
 *   },
 *   ...
 * }
 *
 * Scoped per shop (key includes shop_id) so a customer browsing two
 * different shops doesn't mix their carts together.
 *
 * Usage: include this file at the bottom of public_menu.html after
 * calling initCart(shopId) once the DOM is ready.
 */

"use strict";

// ── Cart state ────────────────────────────────────────────────────────

let _shopId   = null;
let _cartKey  = null;
let _cart     = {};   // in-memory mirror of localStorage

function _storageKey(shopId) {
  return `sajilo_cart_${shopId}`;
}

function _load() {
  try {
    const raw = localStorage.getItem(_cartKey);
    _cart = raw ? JSON.parse(raw) : {};
  } catch (e) {
    // localStorage unavailable (private browsing with storage blocked,
    // or quota exceeded) — fall back to in-memory only. Cart won't
    // persist across page refreshes in this case, but it still works
    // for the current session.
    _cart = {};
    console.warn("Sajilo cart: localStorage unavailable, using in-memory fallback.", e);
  }
}

function _save() {
  try {
    localStorage.setItem(_cartKey, JSON.stringify(_cart));
  } catch (e) {
    console.warn("Sajilo cart: could not save to localStorage.", e);
  }
}

// ── Cart operations ───────────────────────────────────────────────────

function addItem(productId, name, price) {
  productId = String(productId);
  if (_cart[productId]) {
    _cart[productId].quantity += 1;
  } else {
    _cart[productId] = { id: productId, name, price: parseFloat(price), quantity: 1 };
  }
  _save();
  _render();
}

function removeItem(productId) {
  productId = String(productId);
  delete _cart[productId];
  _save();
  _render();
}

function updateQuantity(productId, delta) {
  productId = String(productId);
  if (!_cart[productId]) return;
  _cart[productId].quantity += delta;
  if (_cart[productId].quantity <= 0) {
    delete _cart[productId];
  }
  _save();
  _render();
}

function clearCart() {
  _cart = {};
  _save();
  _render();
}

function getCart() {
  return { ..._cart };
}

function getTotalItems() {
  return Object.values(_cart).reduce((sum, item) => sum + item.quantity, 0);
}

function getTotalPrice() {
  return Object.values(_cart).reduce((sum, item) => sum + item.price * item.quantity, 0);
}

// ── DOM rendering ─────────────────────────────────────────────────────

function _render() {
  _renderBottomBar();
  _renderCartDrawer();
  _renderAddButtons();
}

function _renderBottomBar() {
  const bar        = document.getElementById("cart-bottom-bar");
  const countEl    = document.getElementById("cart-item-count");
  const totalEl    = document.getElementById("cart-total-price");
  if (!bar) return;

  const total = getTotalItems();

  if (total === 0) {
    bar.style.display = "none";
  } else {
    bar.style.display = "flex";
    countEl.textContent = `${total} item${total > 1 ? "s" : ""}`;
    totalEl.textContent = `Rs. ${getTotalPrice().toFixed(2)}`;
  }
}

function _renderCartDrawer() {
  const list = document.getElementById("cart-items-list");
  if (!list) return;

  const items = Object.values(_cart);
  if (items.length === 0) {
    list.innerHTML = `<p class="text-muted text-center py-3">Your cart is empty.</p>`;
    return;
  }

  list.innerHTML = items.map(item => `
    <div class="cart-item d-flex align-items-center gap-2 py-2 border-bottom"
         data-product-id="${item.id}">
      <div class="flex-grow-1">
        <div class="fw-semibold" style="font-size: 0.9rem;">${_escapeHtml(item.name)}</div>
        <div style="font-size: 0.82rem; color: #6c757d;">Rs. ${item.price.toFixed(2)} each</div>
      </div>
      <div class="d-flex align-items-center gap-1">
        <button class="btn btn-sm btn-outline-secondary qty-btn"
                style="width:28px; height:28px; padding:0; line-height:1;"
                onclick="updateQuantity('${item.id}', -1)">−</button>
        <span style="min-width: 24px; text-align: center; font-weight: 600;">
          ${item.quantity}
        </span>
        <button class="btn btn-sm btn-outline-secondary qty-btn"
                style="width:28px; height:28px; padding:0; line-height:1;"
                onclick="updateQuantity('${item.id}', 1)">+</button>
      </div>
      <div style="min-width: 60px; text-align: right; font-weight: 600; font-size: 0.9rem;">
        Rs. ${(item.price * item.quantity).toFixed(2)}
      </div>
      <button class="btn btn-sm btn-link text-danger p-0 ms-1"
              onclick="removeItem('${item.id}')" title="Remove">
        <i class="bi bi-x-circle"></i>
      </button>
    </div>
  `).join("");

  // Update drawer total
  const drawerTotal = document.getElementById("cart-drawer-total");
  if (drawerTotal) {
    drawerTotal.textContent = `Rs. ${getTotalPrice().toFixed(2)}`;
  }
}

function _renderAddButtons() {
  // Update all "Add" buttons on the menu to show "In Cart" state
  document.querySelectorAll("[data-product-id]").forEach(btn => {
    const pid = String(btn.dataset.productId);
    if (_cart[pid]) {
      btn.textContent = "✓ Added";
      btn.classList.add("btn-success");
      btn.classList.remove("btn-sajilo", "btn-outline-sajilo");
    } else {
      btn.textContent = "Add";
      btn.classList.remove("btn-success");
      btn.classList.add("btn-sajilo");
    }
  });
}

// ── Cart drawer toggle ────────────────────────────────────────────────

function openCartDrawer() {
  const drawer = document.getElementById("cart-drawer");
  const overlay = document.getElementById("cart-overlay");
  if (drawer)  drawer.classList.add("open");
  if (overlay) overlay.style.display = "block";
  _renderCartDrawer();
}

function closeCartDrawer() {
  const drawer = document.getElementById("cart-drawer");
  const overlay = document.getElementById("cart-overlay");
  if (drawer)  drawer.classList.remove("open");
  if (overlay) overlay.style.display = "none";
}

// ── Utilities ─────────────────────────────────────────────────────────

function _escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ── Order submission (Day 12) ───────────────────────────────────────
//
// Fetch-based, no page reload — fits the mobile-first / slow-connection
// goal. Talks to orders:place.
//
// cart.js scopes the local cart by the shop's numeric id (matching the
// QR short-link, sajilo_cart_<shop_id>), but orders:place is a
// slug-based URL — so rather than reconstruct it from _shopId, the
// template must set it explicitly before this script runs:
//   <script>window.SAJILO_ORDER_PLACE_URL = "{% url 'orders:place' shop.slug %}";</script>
//
// Expected (optional) DOM elements — read if present, sensible
// defaults used if not, so this doesn't break before the drawer
// template grows these fields:
//   #order-table-number         <input>  free text, e.g. "Table 3"
//   #order-customer-note        <textarea>
//   input[name="payment_method"]:checked   radio group, values "cash" | "fonepay"
//   #place-order-btn            the submit button (disabled while in flight)
//   #order-error                container for inline error messages
//   #cart-drawer-body           swapped for confirmation content on success
//
// Requires getCsrfToken() from main.js, and that the page actually has
// a CSRF cookie set — since public_menu_view serves anonymous
// customers, it needs @ensure_csrf_cookie (or a hidden
// {% csrf_token %} rendered somewhere) or the cookie never gets issued
// and every submit will fail CSRF validation.

let _submitting = false;

function _orderTokenKey(shopId) {
  return `sajilo_order_token_${shopId}`;
}

function _getOrCreateOrderToken() {
  const key = _orderTokenKey(_shopId);
  let token = localStorage.getItem(key);
  if (!token) {
    token = (crypto.randomUUID ? crypto.randomUUID() : _uuidFallback());
    try {
      localStorage.setItem(key, token);
    } catch (e) {
      // localStorage unavailable — order still works, it just loses
      // duplicate-submit protection across page reloads.
    }
  }
  return token;
}

function _clearOrderToken() {
  try {
    localStorage.removeItem(_orderTokenKey(_shopId));
  } catch (e) {
    // ignore
  }
}

function _uuidFallback() {
  // crypto.randomUUID() requires a secure context (https / localhost).
  // Good enough fallback for older/odd mobile browsers on plain http
  // during local dev.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function _setSubmitting(state) {
  _submitting = state;
  const btn = document.getElementById("place-order-btn");
  if (!btn) return;
  btn.disabled = state;
  btn.textContent = state ? "Placing order…" : "Place Order";
}

function _showOrderError(message, unavailableItems) {
  const box = document.getElementById("order-error");
  if (box) {
    box.textContent = message;
    box.style.display = "block";
  } else {
    // No dedicated error slot in the template yet — fall back so the
    // customer still finds out.
    alert(message);
  }

  // Drop any items the server flagged as no longer orderable, so the
  // customer isn't stuck retrying with the same broken cart.
  if (Array.isArray(unavailableItems)) {
    unavailableItems.forEach((item) => {
      if (item && item.id != null) delete _cart[String(item.id)];
    });
    if (unavailableItems.length) {
      _save();
      _render();
    }
  }
}

function _showOrderConfirmation(order) {
  const body = document.getElementById("cart-drawer-body");
  if (!body) {
    alert(`Order ${order.order_number_display} placed! Show this to the shop: ${order.token}`);
    return;
  }

  const itemsHtml = order.items.map(i => `
    <div class="d-flex justify-content-between py-1">
      <span>${_escapeHtml(i.name)} × ${i.quantity}</span>
      <span>Rs. ${i.line_total}</span>
    </div>
  `).join("");

  body.innerHTML = `
    <div class="text-center py-3">
      <div class="fs-4 fw-bold mb-1">✓ Order ${order.order_number_display}</div>
      <div class="text-muted mb-3" style="font-size:0.85rem;">Status: ${order.status}</div>
      <div class="text-start border-top border-bottom py-2 mb-2">${itemsHtml}</div>
      <div class="d-flex justify-content-between fw-bold mb-3">
        <span>Total</span><span>Rs. ${order.subtotal}</span>
      </div>
      <button class="btn btn-sajilo" onclick="SajiloCart.closeDrawer()">Done</button>
    </div>
  `;
}

function submitOrder() {
  if (_submitting) return;
  if (getTotalItems() === 0) {
    _showOrderError("Your cart is empty.");
    return;
  }

  const items = Object.values(_cart).map(item => ({
    id: item.id,
    quantity: item.quantity,
  }));

  const tableInput = document.getElementById("order-table-number");
  const noteInput  = document.getElementById("order-customer-note");
  const paymentInput = document.querySelector('input[name="payment_method"]:checked');

  const payload = {
    items,
    table_number: tableInput ? tableInput.value : "",
    customer_note: noteInput ? noteInput.value : "",
    payment_method: paymentInput ? paymentInput.value : "cash",
    order_token: _getOrCreateOrderToken(),
  };

  const errorBox = document.getElementById("order-error");
  if (errorBox) errorBox.style.display = "none";

  const placeOrderUrl = window.SAJILO_ORDER_PLACE_URL;
  if (!placeOrderUrl) {
    _showOrderError("Order placement isn't configured on this page.");
    return;
  }

  _setSubmitting(true);

  fetch(placeOrderUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRFToken": getCsrfToken(),
    },
    body: JSON.stringify(payload),
  })
    .then(async (res) => {
      const data = await res.json();
      if (res.ok && data.success) {
        clearCart();
        _clearOrderToken();
        _showOrderConfirmation(data.order);
      } else {
        _showOrderError(data.message || "Could not place your order.", data.unavailable_items);
      }
    })
    .catch(() => {
      _showOrderError("Network error — please check your connection and try again.");
    })
    .finally(() => {
      _setSubmitting(false);
    });
}

// ── Init ──────────────────────────────────────────────────────────────

function initCart(shopId) {
  _shopId  = shopId;
  _cartKey = _storageKey(shopId);
  _load();
  _render();
}

// Expose public API only — internal helpers (_load, _save, etc.)
// stay module-private by naming convention rather than true closure
// (since this is a plain script, not a module), but only the
// following should be called from HTML onclick handlers or templates.
window.SajiloCart = {
  init:           initCart,
  add:            addItem,
  remove:         removeItem,
  updateQuantity: updateQuantity,
  clear:          clearCart,
  get:            getCart,
  totalItems:     getTotalItems,
  totalPrice:     getTotalPrice,
  openDrawer:     openCartDrawer,
  closeDrawer:    closeCartDrawer,
  submitOrder:    submitOrder,
};
