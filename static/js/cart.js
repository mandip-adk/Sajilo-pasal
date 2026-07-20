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
};

