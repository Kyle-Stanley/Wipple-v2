(() => {
  const headerLogo = document.querySelector("#headerLogo");
  headerLogo.addEventListener("error", () => {
    headerLogo.outerHTML = "w<span>i</span>pple";
  }, { once: true });

  const heroLogo = document.querySelector("#heroLogo");
  heroLogo.addEventListener("error", () => {
    heroLogo.src = "/static/logo/logo2_bw.png";
  }, { once: true });

  const loaderLogoImage = document.querySelector("#loaderLogoImage");
  loaderLogoImage.addEventListener("error", () => {
    loaderLogoImage.closest(".loader-logo")?.classList.add("noimg");
  }, { once: true });

  const reloadPage = () => location.reload();
  document.querySelector("#newScan").onclick = reloadPage;
  document.querySelector("#startOver").onclick = reloadPage;
})();
