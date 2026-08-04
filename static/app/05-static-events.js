(() => {
  const handleImageError = (image, fallback) => {
    if (!image) return;

    let handled = false;
    const onError = () => {
      if (handled) return;
      handled = true;
      image.removeEventListener("error", onError);
      fallback();
    };
    image.addEventListener("error", onError);
    if (image.complete && image.naturalWidth === 0) onError();
  };

  const headerLogo = document.querySelector("#headerLogo");
  handleImageError(headerLogo, () => {
    headerLogo.outerHTML = "w<span>i</span>pple";
  });

  const heroLogo = document.querySelector("#heroLogo");
  handleImageError(heroLogo, () => {
    heroLogo.src = "/static/logo/logo2_bw.png";
  });

  const loaderLogoImage = document.querySelector("#loaderLogoImage");
  handleImageError(loaderLogoImage, () => {
    loaderLogoImage.closest(".loader-logo")?.classList.add("noimg");
  });

  const reloadPage = () => location.reload();
  document.querySelector("#newScan")?.addEventListener("click", reloadPage);
  document.querySelector("#startOver")?.addEventListener("click", reloadPage);
})();
