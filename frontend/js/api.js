const Api = {
  async analysis(url, pageNumber, isCurrent = () => true) {
    const result = pageNumber === 1
      ? { ok_fdps: [], rejected_fdps: [] }
      : { ok_customers: [], rejected_customers: [] };
    const groups = new Map();
    let cursor = null;
    do {
      if (!isCurrent()) return null;
      const part = await this.get(url + (cursor ? `?after=${encodeURIComponent(cursor)}` : ""));
      if (!isCurrent()) return null;
      if (result.thresholds && JSON.stringify(result.thresholds) !== JSON.stringify(part.thresholds)) {
        throw new Error("Thresholds changed while loading. Select the upload again to refresh.");
      }
      result.thresholds = part.thresholds;
      if (pageNumber === 1) {
        for (const key of ["ok_fdps", "rejected_fdps"]) {
          for (const fdp of part[key]) {
            if (groups.has(fdp.id)) groups.get(fdp.id).customers.push(...fdp.customers);
            else { groups.set(fdp.id, fdp); result[key].push(fdp); }
          }
        }
      } else {
        result.ok_customers.push(...part.ok_customers);
        result.rejected_customers.push(...part.rejected_customers);
      }
      if (part.has_more && (!part.next_cursor || part.next_cursor === cursor)) {
        throw new Error("Could not load the next results page. Please reload.");
      }
      cursor = part.has_more ? part.next_cursor : null;
    } while (cursor);
    return result;
  },
  async post(url) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 90000);
    try {
      const r = await fetch(url, { method: "POST", signal: controller.signal });
      if (!r.ok) {
        const error = new Error((await r.json().catch(() => ({}))).detail || r.statusText);
        error.status = r.status;
        throw error;
      }
      return await r.json();
    } finally { clearTimeout(timer); }
  },
  async get(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
  async put(url, body) {
    const r = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
  async postForm(url, formData) {
    const r = await fetch(url, { method: "POST", body: formData });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
  async delete(url) {
    const r = await fetch(url, { method: "DELETE" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  },
};
