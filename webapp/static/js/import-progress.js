/**
 * Indicador de avance para el formulario "Importar todo desde la carpeta"
 * (pantallas de importacion de SOD y de Licencias).
 *
 * La importacion en si corre de forma sincronica en el servidor (escanea
 * la carpeta y procesa cada archivo antes de responder), asi que no hay
 * todavia un progreso real archivo-por-archivo para mostrar. Esto da
 * feedback inmediato de que el pedido se esta procesando -mientras se
 * espera la respuesta del servidor- y evita que el usuario haga doble
 * click pensando que no funciono.
 *
 * Marcado esperado:
 *
 *   <form data-import-form method="POST">
 *     <button data-import-submit type="submit">...</button>
 *     <div data-import-progress class="folderImportCard__progress isHidden">
 *       ...
 *     </div>
 *   </form>
 */
(function () {
  "use strict";

  document.addEventListener("submit", function (event) {
    var form = event.target.closest("[data-import-form]");
    if (!form) return;

    var submitBtn = form.querySelector("[data-import-submit]");
    var progress = form.querySelector("[data-import-progress]");

    if (submitBtn) {
      submitBtn.disabled = true;
    }
    if (progress) {
      progress.classList.remove("isHidden");
    }
  });
})();
