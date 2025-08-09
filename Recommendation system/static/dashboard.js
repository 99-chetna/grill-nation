import { initializeApp } from "https://www.gstatic.com/firebasejs/10.12.1/firebase-app.js";
import { getAuth, onAuthStateChanged } from "https://www.gstatic.com/firebasejs/10.12.1/firebase-auth.js";
import { getDatabase, ref, child, get } from "https://www.gstatic.com/firebasejs/10.12.1/firebase-database.js";

const firebaseConfig = {
  apiKey: "AIzaSyBcn4k8EfyJcTv25UDgaoNmnA1l0MKK2mw",
  authDomain: "recommendation-system-27e6b.firebaseapp.com",
  databaseURL: "https://recommendation-system-27e6b-default-rtdb.firebaseio.com",
  projectId: "recommendation-system-27e6b",
  storageBucket: "recommendation-system-27e6b.appspot.com",
  messagingSenderId: "644925347402",
  appId: "1:644925347402:web:36225ba2481af5f84538b3"
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const db = getDatabase(app);

onAuthStateChanged(auth, async (user) => {
  if (!user) {
    alert("⚠️ Please log in to access your dashboard.");
    window.location.href = "/";
    return;
  }

  const uid = user.uid;
  const dbRef = ref(db);

  const userSnap = await get(child(dbRef, `users/${uid}`));
  if (userSnap.exists()) {
    document.getElementById("userName").innerText = userSnap.val().name;
  }

  const latestSnap = await get(child(dbRef, `orders/${uid}/latest`));
  if (latestSnap.exists()) {
    const latest = latestSnap.val();
    const items = latest.items || [];
    const cartList = document.getElementById("cartItems");
    cartList.innerHTML = "";
    for (const key in items) {
      const li = document.createElement("li");
      li.textContent = `${items[key].name} x ${items[key].quantity}`;
      cartList.appendChild(li);
    }
    document.getElementById("latestOrder").innerText = `Total ₹${latest.total} placed on ${latest.timestamp}`;
  }

  const historySnap = await get(child(dbRef, `orders/${uid}/history`));
  if (historySnap.exists()) {
    const history = historySnap.val();
    const pastOrdersList = document.getElementById("pastOrders");
    pastOrdersList.innerHTML = "";
    Object.values(history).forEach(order => {
      const li = document.createElement("li");
      li.textContent = `₹${order.total} on ${order.timestamp}`;
      pastOrdersList.appendChild(li);
    });
  }
});
